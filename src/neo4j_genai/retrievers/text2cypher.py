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

import logging
from typing import Callable, Optional

import neo4j
from neo4j.exceptions import CypherSyntaxError, DriverError, Neo4jError
from pydantic import ValidationError

from neo4j_genai.exceptions import (
    RetrieverInitializationError,
    SchemaFetchError,
    SearchValidationError,
    Text2CypherRetrievalError,
)
from neo4j_genai.generation.prompts import Text2CypherTemplate
from neo4j_genai.llm import LLMInterface
from neo4j_genai.retrievers.base import Retriever
from neo4j_genai.schema import get_schema
from neo4j_genai.types import (
    LLMModel,
    Neo4jDriverModel,
    Neo4jSchemaModel,
    RawSearchResult,
    RetrieverResultItem,
    Text2CypherRetrieverModel,
    Text2CypherSearchModel,
)

logger = logging.getLogger(__name__)


class Text2CypherRetriever(Retriever):
    """
    Allows for the retrieval of records from a Neo4j database using natural language.
    Converts a user's natural language query to a Cypher query using an LLM,
    then retrieves records from a Neo4j database using the generated Cypher query

    Args:
        driver (neo4j.driver): The Neo4j Python driver.
        llm (neo4j_genai.generation.llm.LLMInterface): LLM object to generate the Cypher query.
        neo4j_schema (Optional[str]): Neo4j schema used to generate the Cypher query.
        examples (Optional[list[str], optional): Optional user input/query pairs for the LLM to use as examples.
        custom_prompt (Optional[str]): Optional custom prompt to use instead of auto generated prompt. Will not include the neo4j_schema or examples args, if provided.

    Raises:
        RetrieverInitializationError: If validation of the input arguments fail.
    """

    def __init__(
        self,
        driver: neo4j.Driver,
        llm: LLMInterface,
        neo4j_schema: Optional[str] = None,
        examples: Optional[list[str]] = None,
        result_formatter: Optional[
            Callable[[neo4j.Record], RetrieverResultItem]
        ] = None,
        custom_prompt: Optional[str] = None,
    ) -> None:
        try:
            driver_model = Neo4jDriverModel(driver=driver)
            llm_model = LLMModel(llm=llm)
            neo4j_schema_model = (
                Neo4jSchemaModel(neo4j_schema=neo4j_schema) if neo4j_schema else None
            )
            validated_data = Text2CypherRetrieverModel(
                driver_model=driver_model,
                llm_model=llm_model,
                neo4j_schema_model=neo4j_schema_model,
                examples=examples,
                result_formatter=result_formatter,
                custom_prompt=custom_prompt,
            )
        except ValidationError as e:
            raise RetrieverInitializationError(e.errors()) from e

        super().__init__(validated_data.driver_model.driver)
        self.llm = validated_data.llm_model.llm
        self.examples = validated_data.examples
        self.result_formatter = validated_data.result_formatter
        self.custom_prompt = validated_data.custom_prompt
        try:
            if (
                not validated_data.custom_prompt
            ):  # don't need schema for a custom prompt
                self.neo4j_schema = (
                    validated_data.neo4j_schema_model.neo4j_schema
                    if validated_data.neo4j_schema_model
                    else get_schema(validated_data.driver_model.driver)
                )

        except (Neo4jError, DriverError) as e:
            error_message = getattr(e, "message", str(e))
            raise SchemaFetchError(
                f"Failed to fetch schema for Text2CypherRetriever: {error_message}"
            ) from e

    def get_search_results(
        self,
        query_text: str,
    ) -> RawSearchResult:
        """Converts query_text to a Cypher query using an LLM.
           Retrieve records from a Neo4j database using the generated Cypher query.

        Args:
            query_text (str): The natural language query used to search the Neo4j database.

        Raises:
            SearchValidationError: If validation of the input arguments fail.
            Text2CypherRetrievalError: If the LLM fails to generate a correct Cypher query.

        Returns:
            RawSearchResult: The results of the search query as a list of neo4j.Record and an optional metadata dict
        """
        try:
            validated_data = Text2CypherSearchModel(query_text=query_text)
        except ValidationError as e:
            raise SearchValidationError(e.errors()) from e

        if not self.custom_prompt:
            prompt_template = Text2CypherTemplate()
            prompt = prompt_template.format(
                schema=self.neo4j_schema,
                examples="\n".join(self.examples) if self.examples else "",
                query=validated_data.query_text,
            )
        else:
            prompt = self.custom_prompt

        logger.debug("Text2CypherRetriever prompt: %s", prompt)

        try:
            llm_result = self.llm.invoke(prompt)
            t2c_query = llm_result.content
            logger.debug("Text2CypherRetriever Cypher query: %s", t2c_query)
            records, _, _ = self.driver.execute_query(query_=t2c_query)
        except CypherSyntaxError as e:
            raise Text2CypherRetrievalError(
                f"Failed to get search result: {e.message}"
            ) from e

        return RawSearchResult(
            records=records,
            metadata={
                "cypher": t2c_query,
            },
        )
