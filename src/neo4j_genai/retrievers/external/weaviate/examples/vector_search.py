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

from neo4j_genai.retrievers.external.weaviate import WeaviateNeo4jRetriever
from neo4j import GraphDatabase
import weaviate
import sys
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(f"{BASE_DIR}/../../../../../../tests/e2e/weaviate_e2e")

from utils import get_query_vector  # noqa

NEO4J_URL = "neo4j://localhost:7687"
NEO4J_AUTH = ("neo4j", "password")


def main():
    with GraphDatabase.driver(NEO4J_URL, auth=NEO4J_AUTH) as neo4j_driver:
        with weaviate.connect_to_local() as w_client:
            retriever = WeaviateNeo4jRetriever(
                driver=neo4j_driver,
                client=w_client,
                collection="Jeopardy",
                id_property_external="neo4j_id",
                id_property_neo4j="id",
            )
            res = retriever.search(query_vector=get_query_vector(), top_k=2)
            print(res)


if __name__ == "__main__":
    main()
