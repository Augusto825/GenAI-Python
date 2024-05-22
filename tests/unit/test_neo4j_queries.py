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
from unittest.mock import patch

from neo4j_genai.neo4j_queries import get_search_query, get_query_tail
from neo4j_genai.types import SearchType


def test_vector_search_basic():
    expected = (
        "CALL db.index.vector.queryNodes($vector_index_name, $top_k, $query_vector) "
        "YIELD node, score "
        "RETURN node, score"
    )
    result, params = get_search_query(SearchType.VECTOR)
    assert result.strip() == expected.strip()
    assert params == {}


def test_hybrid_search_basic():
    expected = (
        "CALL { "
        "CALL db.index.vector.queryNodes($vector_index_name, $top_k, $query_vector) "
        "YIELD node, score "
        "RETURN node, score UNION "
        "CALL db.index.fulltext.queryNodes($fulltext_index_name, $query_text, {limit: $top_k}) "
        "YIELD node, score "
        "WITH collect({node:node, score:score}) AS nodes, max(score) AS max "
        "UNWIND nodes AS n "
        "RETURN n.node AS node, (n.score / max) AS score "
        "} "
        "WITH node, max(score) AS score ORDER BY score DESC LIMIT $top_k "
        "RETURN node, score"
    )
    result, _ = get_search_query(SearchType.HYBRID)
    assert result.strip() == expected.strip()


def test_vector_search_with_properties():
    properties = ["name", "age"]
    expected = (
        "CALL db.index.vector.queryNodes($vector_index_name, $top_k, $query_vector) "
        "YIELD node, score "
        "RETURN node {.name, .age} as node, score"
    )
    result, _ = get_search_query(SearchType.VECTOR, return_properties=properties)
    assert result.strip() == expected.strip()


def test_vector_search_with_retrieval_query():
    retrieval_query = "MATCH (n) RETURN n LIMIT 10"
    expected = (
        "CALL db.index.vector.queryNodes($vector_index_name, $top_k, $query_vector) "
        "YIELD node, score " + retrieval_query
    )
    result, _ = get_search_query(SearchType.VECTOR, retrieval_query=retrieval_query)
    assert result.strip() == expected.strip()


@patch("neo4j_genai.neo4j_queries.get_metadata_filter", return_value=["True", {}])
def test_vector_search_with_filters(_mock):
    expected = (
        "MATCH (node:`Label`) "
        "WHERE node.`vector` IS NOT NULL "
        "AND size(node.`vector`) = toInteger($embedding_dimension)"
        " AND (True) "
        "WITH node, "
        "vector.similarity.cosine(node.`vector`, $query_vector) AS score "
        "ORDER BY score DESC LIMIT $top_k"
        " RETURN node, score"
    )
    result, params = get_search_query(
        SearchType.VECTOR,
        node_label="Label",
        embedding_node_property="vector",
        embedding_dimension=1,
        filters={"field": "value"},
    )
    assert result.strip() == expected.strip()
    assert params == {"embedding_dimension": 1}


@patch(
    "neo4j_genai.neo4j_queries.get_metadata_filter",
    return_value=["True", {"param": "value"}],
)
def test_vector_search_with_params_from_filters(_mock):
    expected = (
        "MATCH (node:`Label`) "
        "WHERE node.`vector` IS NOT NULL "
        "AND size(node.`vector`) = toInteger($embedding_dimension)"
        " AND (True) "
        "WITH node, "
        "vector.similarity.cosine(node.`vector`, $query_vector) AS score "
        "ORDER BY score DESC LIMIT $top_k"
        " RETURN node, score"
    )
    result, params = get_search_query(
        SearchType.VECTOR,
        node_label="Label",
        embedding_node_property="vector",
        embedding_dimension=1,
        filters={"field": "value"},
    )
    assert result.strip() == expected.strip()
    assert params == {"embedding_dimension": 1, "param": "value"}


def test_hybrid_search_with_retrieval_query():
    retrieval_query = "MATCH (n) RETURN n LIMIT 10"
    expected = (
        "CALL { "
        "CALL db.index.vector.queryNodes($vector_index_name, $top_k, $query_vector) "
        "YIELD node, score "
        "RETURN node, score UNION "
        "CALL db.index.fulltext.queryNodes($fulltext_index_name, $query_text, {limit: $top_k}) "
        "YIELD node, score "
        "WITH collect({node:node, score:score}) AS nodes, max(score) AS max "
        "UNWIND nodes AS n "
        "RETURN n.node AS node, (n.score / max) AS score "
        "} "
        "WITH node, max(score) AS score ORDER BY score DESC LIMIT $top_k "
        + retrieval_query
    )
    result, _ = get_search_query(SearchType.HYBRID, retrieval_query=retrieval_query)
    assert result.strip() == expected.strip()


def test_hybrid_search_with_properties():
    properties = ["name", "age"]
    expected = (
        "CALL { "
        "CALL db.index.vector.queryNodes($vector_index_name, $top_k, $query_vector) "
        "YIELD node, score "
        "RETURN node, score UNION "
        "CALL db.index.fulltext.queryNodes($fulltext_index_name, $query_text, {limit: $top_k}) "
        "YIELD node, score "
        "WITH collect({node:node, score:score}) AS nodes, max(score) AS max "
        "UNWIND nodes AS n "
        "RETURN n.node AS node, (n.score / max) AS score "
        "} "
        "WITH node, max(score) AS score ORDER BY score DESC LIMIT $top_k "
        "RETURN node {.name, .age} as node, score"
    )
    result, _ = get_search_query(SearchType.HYBRID, return_properties=properties)
    assert result.strip() == expected.strip()


def test_get_query_tail_with_retrieval_query():
    retrieval_query = "MATCH (n) RETURN n LIMIT 10"
    expected = retrieval_query
    result = get_query_tail(retrieval_query=retrieval_query)
    assert result.strip() == expected.strip()


def test_get_query_tail_with_properties():
    properties = ["name", "age"]
    expected = "RETURN node {.name, .age} as node, score"
    result = get_query_tail(return_properties=properties)
    assert result.strip() == expected.strip()


def test_get_query_tail_with_fallback():
    fallback = "HELLO"
    expected = fallback
    result = get_query_tail(fallback_return=fallback)
    assert result.strip() == expected.strip()


def test_get_query_tail_ordering_all():
    retrieval_query = "MATCH (n) RETURN n LIMIT 10"
    properties = ["name", "age"]
    fallback = "HELLO"

    expected = retrieval_query
    result = get_query_tail(
        retrieval_query=retrieval_query,
        return_properties=properties,
        fallback_return=fallback,
    )
    assert result.strip() == expected.strip()


def test_get_query_tail_ordering_no_retrieval_query():
    properties = ["name", "age"]
    fallback = "HELLO"

    expected = "RETURN node {.name, .age} as node, score"
    result = get_query_tail(
        return_properties=properties,
        fallback_return=fallback,
    )
    assert result.strip() == expected.strip()
