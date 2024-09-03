#  Copyright (c) "Neo4j"
#  Neo4j Sweden AB [https://neo4j.com]
#  #
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#  #
#      https://www.apache.org/licenses/LICENSE-2.0
#  #
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
from __future__ import annotations

import asyncio
import enum
import logging
import warnings
from datetime import datetime
from functools import partial
from timeit import default_timer
from typing import Any, AsyncGenerator, Optional, Protocol

from pydantic import BaseModel, Field

from neo4j_genai.experimental.pipeline.component import Component, DataModel
from neo4j_genai.experimental.pipeline.exceptions import (
    PipelineDefinitionError,
    PipelineMissingDependencyError,
    PipelineStatusUpdateError,
)
from neo4j_genai.experimental.pipeline.pipeline_graph import (
    PipelineEdge,
    PipelineGraph,
    PipelineNode,
)
from neo4j_genai.experimental.pipeline.stores import InMemoryStore, Store
from neo4j_genai.experimental.pipeline.types import (
    ComponentConfig,
    ConnectionConfig,
    PipelineConfig,
)

logger = logging.getLogger(__name__)


class RunStatus(enum.Enum):
    UNKNOWN = "UNKNOWN"
    SCHEDULED = "SCHEDULED"
    WAITING = "WAITING"
    RUNNING = "RUNNING"
    SKIP = "SKIP"
    DONE = "DONE"


class RunResult(BaseModel):
    status: RunStatus
    result: Optional[DataModel] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# Type used for annotating the partial on_task_complete parameter
# and prevent type checking error when using kwargs
class PartialOnTaskCompleteProtocol(Protocol):
    async def __call__(self, task: TaskPipelineNode, res: RunResult) -> None: ...


class TaskPipelineNode(PipelineNode):
    """Runnable node. It must have:
    - a name (unique within the pipeline)
    - a component instance
    """

    def __init__(self, name: str, component: Component):
        """TaskPipelineNode is a graph node with a run method.

        Args:
            name (str): node's name
            component (Component): component instance
        """
        super().__init__(name, {})
        self.component = component
        self.status = RunStatus.UNKNOWN
        self._lock = asyncio.Lock()
        """This lock is used to make sure we're not trying
        to update the status in //. This should prevent the task to
        be executed multiple times because the status was not known
        by the orchestrator.
        """

    async def set_status(self, status: RunStatus) -> None:
        """Set a new status

        Args:
            status (RunStatus): new status

        Raises:
            StatusUpdateError if the new status is not
                compatible with the current one.
        """
        async with self._lock:
            if status == self.status:
                raise PipelineStatusUpdateError()
            if status == RunStatus.RUNNING and self.status == RunStatus.DONE:
                # can't go back to RUNNING from DONE
                raise PipelineStatusUpdateError()
            self.status = status

    async def read_status(self) -> RunStatus:
        async with self._lock:
            return self.status

    async def execute(self, **kwargs: Any) -> RunResult | None:
        """Execute the task:
        1. Set status to RUNNING
        2. Calls the component.run method
        3. Set status to DONE

        Returns:
            RunResult | None: RunResult with status and result dict
            if the task run successfully, None if the status update
            was unsuccessful.
        """
        logger.debug(f"Running component {self.name} with {kwargs}")
        start_time = default_timer()
        try:
            await self.set_status(RunStatus.RUNNING)
        except PipelineStatusUpdateError:
            logger.info(f"Component {self.name} already running or done {self.status}")
            return None
        component_result = await self.component.run(**kwargs)
        await self.set_status(RunStatus.DONE)
        run_result = RunResult(
            status=self.status,
            result=component_result,
        )
        end_time = default_timer()
        logger.debug(f"Component {self.name} finished in {end_time - start_time}s")
        return run_result

    def reinitialize(self) -> None:
        self.status = RunStatus.SCHEDULED

    async def run(
        self, inputs: dict[str, Any], callback: PartialOnTaskCompleteProtocol
    ) -> None:
        """Main method to execute the task."""
        logger.debug(f"TASK START {self.name=} {inputs=}")
        res = await self.execute(**inputs)
        if res is None:
            return
        logger.debug(f"TASK RESULT {self.name=} {res=}")
        # save its results
        await callback(task=self, res=res)


class Orchestrator:
    """Orchestrate a pipeline.

    The orchestrator is responsible for:
    - finding the next tasks to execute
    - building the inputs for each task
    - calling the run method on each task

    Once a TaskNode is done, it calls the `on_task_complete` callback
    that will save the results, find the next tasks to be executed
    (checking that all dependencies are met), and run them.
    """

    def __init__(self, pipeline: "Pipeline"):
        self.pipeline = pipeline

    async def run_task(self, task: TaskPipelineNode, data: dict[str, Any]) -> None:
        """Get inputs and run a specific task. Once the task is done,
        calls the on_task_complete method.

        Args:
            task (TaskPipelineNode): The task to be run
            data (dict[str, Any]): The pipeline input data

        Returns:
            None
        """
        input_config = await self.get_input_config_for_task(task)
        inputs = self.get_component_inputs(task.name, input_config, data)
        await task.run(inputs, callback=partial(self.on_task_complete, data=data))

    async def on_task_complete(
        self, data: dict[str, Any], task: TaskPipelineNode, res: RunResult
    ) -> None:
        """When a given task is complete, it will call this method
        to find the next tasks to run.
        """
        # first call the method for the pipeline
        # this is where the results can be saved
        self.pipeline.on_task_complete(task, res)
        # then get the next tasks to be executed
        # and run them in //
        await asyncio.gather(*[self.run_task(n, data) async for n in self.next(task)])

    async def check_dependencies_complete(self, task: TaskPipelineNode) -> None:
        """Check that all parent tasks are complete.

        Raises:
            MissingDependencyError if a parent task's status is not DONE.
        """
        dependencies = self.pipeline.previous_edges(task.name)
        for d in dependencies:
            start_node = self.pipeline.get_node_by_name(d.start)
            d_status = await start_node.read_status()
            if d_status != RunStatus.DONE:
                logger.warning(
                    f"Missing dependency {d.start} for {task.name} (status: {d_status})"
                )
                raise PipelineMissingDependencyError()

    async def next(
        self, task: TaskPipelineNode
    ) -> AsyncGenerator[TaskPipelineNode, None]:
        """Find the next tasks to be executed after `task` is complete.

        1. Find the task children
        2. Check each child's status:
            - if it's already running or done, do not need to run it again
            - otherwise, check that all its dependencies are met, if yes
                add this task to the list of next tasks to be executed
        """
        possible_nexts = self.pipeline.next_edges(task.name)
        for next_edge in possible_nexts:
            next_node = self.pipeline.get_node_by_name(next_edge.end)
            # check status
            next_node_status = await next_node.read_status()
            if next_node_status in [RunStatus.RUNNING, RunStatus.DONE]:
                # already running
                continue
            # check deps
            try:
                await self.check_dependencies_complete(next_node)
            except PipelineMissingDependencyError:
                continue
            yield next_node
        return

    async def get_input_config_for_task(self, task: TaskPipelineNode) -> dict[str, str]:
        """Build input definition for a given task.,
        The method needs to access the input defs defined in the edges
        between this task and its parents.

        Args:
            task (TaskPipelineNode): the task to get the input config for

        Returns:
            dict: a dict of
                {input_parameter: path_to_value_in_the_pipeline_results_dict}
        """
        input_config: dict[str, Any] = {}
        # make sure dependencies are satisfied
        # and save the inputs defs that needs to be propagated from parent components
        for prev_edge in self.pipeline.previous_edges(task.name):
            prev_node = self.pipeline.get_node_by_name(prev_edge.start)
            prev_status = await prev_node.read_status()
            if prev_status != RunStatus.DONE:
                logger.critical(f"Missing dependency {prev_edge.start}")
                raise PipelineMissingDependencyError(f"{prev_edge.start} not ready")
            if prev_edge.data:
                prev_edge_data = prev_edge.data.get("input_config") or {}
                input_config.update(**prev_edge_data)
        return input_config

    def get_component_inputs(
        self,
        component_name: str,
        input_config: dict[str, Any],
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Find the component inputs from:
        - input_config: the mapping between components results and inputs
            (results are stored in the pipeline result store)
        - input_data: the user input data

        Args:
            component_name (str): the component/task name
            input_config (dict[str, Any]): the input config
            input_data (dict[str, Any]): the pipeline input data (user input)
        """
        component_inputs: dict[str, Any] = input_data.get(component_name, {})
        if input_config:
            for parameter, mapping in input_config.items():
                try:
                    component, output_param = mapping.split(".")
                except ValueError:
                    # we will use the full output of
                    # component as input
                    component = mapping
                    output_param = None
                component_result = self.pipeline.get_results_for_component(component)
                if output_param is not None:
                    value = component_result.get(output_param)
                else:
                    value = component_result
                if parameter in component_inputs:
                    warnings.warn(
                        f"In component '{component_name}', parameter '{parameter}' from user input will be ignored and replaced by '{mapping}'"
                    )
                component_inputs[parameter] = value
        return component_inputs

    async def run(self, data: dict[str, Any]) -> None:
        """Run the pipline, starting from the root nodes
        (node without any parent). Then the callback on_task_complete
        will handle the task dependencies.
        """
        logger.debug(f"PIPELINE START {data=}")
        tasks = [self.run_task(root, data) for root in self.pipeline.roots()]
        await asyncio.gather(*tasks)


class Pipeline(PipelineGraph[TaskPipelineNode, PipelineEdge]):
    """This is the main pipeline, where components
    and their execution order are defined"""

    def __init__(self, store: Optional[Store] = None) -> None:
        super().__init__()
        self._store = store or InMemoryStore()
        self._final_results = InMemoryStore()

    @classmethod
    def from_template(
        cls, pipeline_template: PipelineConfig, store: Optional[Store] = None
    ) -> Pipeline:
        """Create a Pipeline from a pydantic model defining the components and their connections"""
        pipeline = Pipeline(store=store)
        for component in pipeline_template.components:
            pipeline.add_component(
                component.component,
                component.name,
            )
        for edge in pipeline_template.connections:
            pipeline_edge = PipelineEdge(
                edge.start, edge.end, data={"input_config": edge.input_config}
            )
            pipeline.add_edge(pipeline_edge)
        return pipeline

    def show_as_dict(self) -> dict[str, Any]:
        component_config = []
        for name, task in self._nodes.items():
            component_config.append(
                ComponentConfig(name=name, component=task.component)
            )
        connection_config = []
        for edge in self._edges:
            connection_config.append(
                ConnectionConfig(
                    start=edge.start,
                    end=edge.end,
                    input_config=edge.data["input_config"] if edge.data else {},
                )
            )
        pipeline_config = PipelineConfig(
            components=component_config, connections=connection_config
        )
        return pipeline_config.model_dump()

    def add_component(self, component: Component, name: str) -> None:
        """Add a new component. Components are uniquely identified
        by their name. If 'name' is already in the pipeline, a ValueError
        is raised."""
        task = TaskPipelineNode(name, component)
        self.add_node(task)

    def set_component(self, name: str, component: Component) -> None:
        """Replace a component with another. If 'name' is not yet in the pipeline,
        raises ValueError.
        """
        task = TaskPipelineNode(name, component)
        self.set_node(task)

    def connect(
        self,
        start_component_name: str,
        end_component_name: str,
        input_config: Optional[dict[str, str]] = None,
    ) -> None:
        """Connect one component to another.

        Args:
            start_component_name (str): name of the component as defined in
                the add_component method
            end_component_name (str): name of the component as defined in
                the add_component method
            input_config (Optional[dict[str, str]]): end component input configuration:
                propagate previous components outputs.

        Raises:
            PipelineDefinitionError: if the provided component are not in the Pipeline
                or if the graph that would be created by this connection is cyclic.
        """
        edge = PipelineEdge(
            start_component_name,
            end_component_name,
            data={"input_config": input_config},
        )
        try:
            self.add_edge(edge)
        except KeyError:
            raise PipelineDefinitionError(
                f"{start_component_name} or {end_component_name} is not in the Pipeline"
            )
        if self.is_cyclic():
            raise PipelineDefinitionError("Cyclic graph are not allowed")

    def on_task_complete(self, task: TaskPipelineNode, result: RunResult) -> None:
        """Method called when a task is done."""
        res_to_save = None
        if result.result:
            res_to_save = result.result.model_dump()
        self.add_result_for_component(task.name, res_to_save, is_final=task.is_leaf())

    def add_result_for_component(
        self, name: str, result: dict[str, Any] | None, is_final: bool = False
    ) -> None:
        """This is where we save the results in the result store and, optionally,
        in the final result store.
        """
        self._store.add(name, result)
        if is_final:
            # The pipeline only returns the results
            # of the leaf nodes
            # TODO: make this configurable in the future.
            self._final_results.add(name, result)

    def get_results_for_component(self, name: str) -> Any:
        return self._store.get(name)

    def reinitialize(self) -> None:
        """Reinitialize the result stores and component status
        if we want to rerun the same pipeline again
        (maybe with inputs)"""
        self._store.empty()
        self._final_results.empty()
        for task in self._nodes.values():
            task.reinitialize()

    def validate_inputs_config(self, data: dict[str, Any]) -> None:
        """Go through the graph and make sure each component will not miss any input

        Args:
            data (dict[str, Any]): the user provided data in the pipeline.run method.
        """
        for task in self._nodes.values():
            self.validate_inputs_config_for_task(task, data)

    def validate_inputs_config_for_task(
        self, task: TaskPipelineNode, input_data: dict[str, Any]
    ) -> bool:
        """Make sure the parameter defined in the input config
        matches a parameter in the previous component output model.
        """
        component = task.component
        expected_mandatory_inputs = [
            param_name
            for param_name, config in component.component_inputs.items()
            if config["has_default"] is False
        ]
        # start building the actual input list, starting
        # from the inputs provided in the pipeline.run method
        actual_inputs = list(input_data.get(task.name, {}).keys())
        prev_edges = self.previous_edges(task.name)
        # then, iterate over all parents to find the parameter propagation
        for edge in prev_edges:
            edge_data = edge.data or {}
            edge_inputs = edge_data.get("input_config") or {}
            # check that the previous component is actually returning
            # the mapped parameter
            for param, path in edge_inputs.items():
                try:
                    source_component_name, param_name = path.split(".")
                except ValueError:
                    # no specific output mapped
                    # the full source component result will be
                    # passed to the next component, so no need
                    # for further check
                    continue
                source_node = self.get_node_by_name(source_component_name)
                source_component = source_node.component
                source_component_outputs = source_component.component_outputs
                if param_name and param_name not in source_component_outputs:
                    raise PipelineDefinitionError(
                        f"Parameter {param_name} is not valid output for "
                        f"{source_component_name} (must be one of "
                        f"{list(source_component_outputs.keys())})"
                    )

            actual_inputs.extend(list(edge_inputs.keys()))
        if set(expected_mandatory_inputs) - set(actual_inputs):
            raise PipelineDefinitionError(
                f"Missing input parameters for {task.name}: "
                f"Expected parameters: {expected_mandatory_inputs}. "
                f"Got: {actual_inputs}"
            )
        return True

    async def run(self, data: dict[str, Any]) -> dict[str, Any]:
        logger.debug("Starting pipeline")
        start_time = default_timer()
        self.validate_inputs_config(data)
        self.reinitialize()
        orchestrator = Orchestrator(self)
        await orchestrator.run(data)
        end_time = default_timer()
        logger.debug(f"Pipeline finished in {end_time - start_time}s")
        return self._final_results.all()
