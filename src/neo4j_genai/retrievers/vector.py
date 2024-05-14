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
from typing import Optional, Any

import neo4j
from neo4j_genai.retrievers.base import Retriever
from pydantic import ValidationError

from neo4j_genai.embedder import Embedder
from neo4j_genai.types import (
    VectorSearchRecord,
    VectorSearchModel,
    VectorCypherSearchModel,
    SearchType,
    Neo4jDriverModel,
    EmbedderModel,
    VectorRetrieverModel,
    VectorCypherRetrieverModel,
)
from neo4j_genai.neo4j_queries import get_search_query
import logging

logger = logging.getLogger(__name__)


class VectorRetriever(Retriever):
    """
    Provides retrieval method using vector search over embeddings.
    If an embedder is provided, it needs to have the required Embedder type.
    """

    def __init__(
        self,
        driver: neo4j.Driver,
        index_name: str,
        embedder: Optional[Embedder] = None,
        return_properties: Optional[list[str]] = None,
    ) -> None:
        try:
            driver_model = Neo4jDriverModel(driver=driver)
            embedder_model = EmbedderModel(embedder=embedder) if embedder else None
            validated_data = VectorRetrieverModel(
                driver_model=driver_model,
                index_name=index_name,
                embedder_model=embedder_model,
                return_properties=return_properties,
            )
        except ValidationError as e:
            raise ValueError(f"Validation failed: {e.errors()}")

        super().__init__(driver)
        self.index_name = validated_data.index_name
        self.return_properties = validated_data.return_properties
        self.embedder = (
            validated_data.embedder_model.embedder
            if validated_data.embedder_model
            else None
        )
        self._node_label = None
        self._embedding_node_property = None
        self._embedding_dimension = None
        self._fetch_index_infos()

    def search(
        self,
        query_vector: Optional[list[float]] = None,
        query_text: Optional[str] = None,
        top_k: int = 5,
        filters: Optional[dict[str, Any]] = None,
    ) -> list[VectorSearchRecord]:
        """Get the top_k nearest neighbor embeddings for either provided query_vector or query_text.
        See the following documentation for more details:

        - [Query a vector index](https://neo4j.com/docs/cypher-manual/current/indexes-for-vector-search/#indexes-vector-query)
        - [db.index.vector.queryNodes()](https://neo4j.com/docs/operations-manual/5/reference/procedures/#procedure_db_index_vector_queryNodes)

        Args:
            query_vector (Optional[list[float]], optional): The vector embeddings to get the closest neighbors of. Defaults to None.
            query_text (Optional[str], optional): The text to get the closest neighbors of. Defaults to None.
            top_k (int, optional): The number of neighbors to return. Defaults to 5.

        Raises:
            ValueError: If validation of the input arguments fail.
            ValueError: If no embedder is provided.

        Returns:
            list[VectorSearchRecord]: The `top_k` neighbors found in vector search with their nodes and scores.
        """
        try:
            validated_data = VectorSearchModel(
                vector_index_name=self.index_name,
                top_k=top_k,
                query_vector=query_vector,
                query_text=query_text,
            )
        except ValidationError as e:
            error_details = e.errors()
            raise ValueError(f"Validation failed: {error_details}")

        parameters = validated_data.model_dump(exclude_none=True)

        if query_text:
            if not self.embedder:
                raise ValueError("Embedding method required for text query.")
            query_vector = self.embedder.embed_query(query_text)
            parameters["query_vector"] = query_vector
            del parameters["query_text"]

        search_query, search_params = get_search_query(
            SearchType.VECTOR,
            self.return_properties,
            node_label=self._node_label,
            embedding_node_property=self._embedding_node_property,
            embedding_dimension=self._embedding_dimension,
            filters=filters,
        )
        parameters.update(search_params)

        logger.debug("VectorRetriever Cypher parameters: %s", parameters)
        logger.debug("VectorRetriever Cypher query: %s", search_query)

        records, _, _ = self.driver.execute_query(search_query, parameters)

        try:
            return [
                VectorSearchRecord(node=record["node"], score=record["score"])
                for record in records
            ]
        except ValidationError as e:
            error_details = e.errors()
            raise ValueError(
                f"Validation failed while constructing output: {error_details}"
            )


class VectorCypherRetriever(Retriever):
    """
    Provides retrieval method using vector similarity and custom Cypher query.
    If an embedder is provided, it needs to have the required Embedder type.
    """

    def __init__(
        self,
        driver: neo4j.Driver,
        index_name: str,
        retrieval_query: str,
        embedder: Optional[Embedder] = None,
    ) -> None:
        try:
            driver_model = Neo4jDriverModel(driver=driver)
            embedder_model = EmbedderModel(embedder=embedder) if embedder else None
            validated_data = VectorCypherRetrieverModel(
                driver_model=driver_model,
                index_name=index_name,
                retrieval_query=retrieval_query,
                embedder_model=embedder_model,
            )
        except ValidationError as e:
            raise ValueError(f"Validation failed: {e.errors()}")

        super().__init__(driver)
        self.index_name = validated_data.index_name
        self.retrieval_query = validated_data.retrieval_query
        self.embedder = (
            validated_data.embedder_model.embedder
            if validated_data.embedder_model
            else None
        )
        self._node_label = None
        self._node_embedding_property = None
        self._embedding_dimension = None
        self._fetch_index_infos()

    def search(
        self,
        query_vector: Optional[list[float]] = None,
        query_text: Optional[str] = None,
        top_k: int = 5,
        query_params: Optional[dict[str, Any]] = None,
        filters: Optional[dict[str, Any]] = None,
    ) -> list[neo4j.Record]:
        """Get the top_k nearest neighbor embeddings for either provided query_vector or query_text.
        See the following documentation for more details:

        - [Query a vector index](https://neo4j.com/docs/cypher-manual/current/indexes-for-vector-search/#indexes-vector-query)
        - [db.index.vector.queryNodes()](https://neo4j.com/docs/operations-manual/5/reference/procedures/#procedure_db_index_vector_queryNodes)

        Args:
            query_vector (Optional[list[float]], optional): The vector embeddings to get the closest neighbors of. Defaults to None.
            query_text (Optional[str], optional): The text to get the closest neighbors of. Defaults to None.
            top_k (int, optional): The number of neighbors to return. Defaults to 5.
            query_params (Optional[dict[str, Any]], optional): Parameters for the Cypher query. Defaults to None.
            filters (Optional[dict[str, Any]], optional): Filters for metadata pre-filtering.. Defaults to None.

        Raises:
            ValueError: If validation of the input arguments fail.
            ValueError: If no embedder is provided.

        Returns:
            list[neo4j.Record]: The results of the search query
        """
        try:
            validated_data = VectorCypherSearchModel(
                vector_index_name=self.index_name,
                top_k=top_k,
                query_vector=query_vector,
                query_text=query_text,
                query_params=query_params,
            )
        except ValidationError as e:
            raise ValueError(f"Validation failed: {e.errors()}")

        parameters = validated_data.model_dump(exclude_none=True)

        if query_text:
            if not self.embedder:
                raise ValueError("Embedding method required for text query.")
            parameters["query_vector"] = self.embedder.embed_query(query_text)
            del parameters["query_text"]

        if query_params:
            for key, value in query_params.items():
                if key not in parameters:
                    parameters[key] = value
            del parameters["query_params"]

        search_query, search_params = get_search_query(
            SearchType.VECTOR,
            retrieval_query=self.retrieval_query,
            node_label=self._node_label,
            embedding_node_property=self._node_embedding_property,
            embedding_dimension=self._embedding_dimension,
            filters=filters,
        )
        parameters.update(search_params)

        logger.debug("VectorCypherRetriever Cypher parameters: %s", parameters)
        logger.debug("VectorCypherRetriever Cypher query: %s", search_query)

        records, _, _ = self.driver.execute_query(search_query, parameters)
        return records
