from typing import List, Optional
from pydantic import ValidationError
from neo4j import Driver
from .embedder import Embedder
from .types import SimilaritySearchModel, Neo4jRecord


class VectorRetriever:
    """
    Provides retrieval methods using vector search over embeddings
    """

    def __init__(
        self,
        driver: Driver,
        index_name: str,
        embedder: Optional[Embedder] = None,
    ) -> None:
        self.driver = driver
        self._verify_version()
        self.index_name = index_name
        self.embedder = embedder

    def _verify_version(self) -> None:
        """
        Check if the connected Neo4j database version supports vector indexing.

        Queries the Neo4j database to retrieve its version and compares it
        against a target version (5.18.1) that is known to support vector
        indexing. Raises a ValueError if the connected Neo4j version is
        not supported.
        """
        records, _, _ = self.driver.execute_query("CALL dbms.components()")
        version = records[0]["versions"][0]

        if "aura" in version:
            version_tuple = (
                *tuple(map(int, version.split("-")[0].split("."))),
                0,
            )
            target_version = (5, 18, 0)
        else:
            version_tuple = tuple(map(int, version.split(".")))
            target_version = (5, 18, 1)

        if version_tuple < target_version:
            raise ValueError(
                "This package only supports Neo4j version 5.18.1 or greater"
            )

    def search(
        self,
        query_vector: Optional[List[float]] = None,
        query_text: Optional[str] = None,
        top_k: int = 5,
    ) -> List[Neo4jRecord]:
        """Get the top_k nearest neighbor embeddings for either provided query_vector or query_text.
        See the following documentation for more details:

        - [Query a vector index](https://neo4j.com/docs/cypher-manual/current/indexes-for-vector-search/#indexes-vector-query)
        - [db.index.vector.queryNodes()](https://neo4j.com/docs/operations-manual/5/reference/procedures/#procedure_db_index_vector_queryNodes)

        Args:
            name (str): Refers to the unique name of the vector index to query.
            query_vector (Optional[List[float]], optional): The vector embeddings to get the closest neighbors of. Defaults to None.
            query_text (Optional[str], optional): The text to get the closest neighbors of. Defaults to None.
            top_k (int, optional): The number of neighbors to return. Defaults to 5.

        Raises:
            ValueError: If validation of the input arguments fail.
            ValueError: If no embedder is provided.

        Returns:
            List[Neo4jRecord]: The `top_k` neighbors found in vector search with their nodes and scores.
        """
        try:
            validated_data = SimilaritySearchModel(
                index_name=self.index_name,
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

        db_query_string = """
        CALL db.index.vector.queryNodes($index_name, $top_k, $query_vector)
        YIELD node, score
        """
        records, _, _ = self.driver.execute_query(db_query_string, parameters)

        try:
            return [
                Neo4jRecord(node=record["node"], score=record["score"])
                for record in records
            ]
        except ValidationError as e:
            error_details = e.errors()
            raise ValueError(
                f"Validation failed while constructing output: {error_details}"
            )
