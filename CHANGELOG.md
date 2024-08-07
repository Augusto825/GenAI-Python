# @neo4j/neo4j-genai-python

## Next

### Added
- Add optional custom_prompt arg to the Text2CypherRetriever class.
  
## 0.3.1

### Fixed
-   Corrected initialization to allow specifying the embedding model name.
-   Removed sentence_transformers from embeddings/__init__.py to avoid ImportError when the package is not installed.

### Changed
- `GraphRAG.search` method first parameter has been renamed `query_text` (was `query`) for consistency with the retrievers interface.

## 0.3.0

### Added
-   Stopped embeddings from being returned when searching with `VectorRetriever`. Added `nodeLabels` and `id` to the metadata of `VectorRetriever` results.
-   Added `upsert_vector` utility function for attaching vectors to node properties.
-   Introduced `Neo4jInsertionError` for handling insertion failures in Neo4j.
-   Included Pinecone and Weaviate retrievers in neo4j_genai.retrievers.
-   Introduced the GraphRAG object, enabling a full RAG (Retrieval-Augmented Generation) pipeline with context retrieval, prompt formatting, and answer generation.
-   Added PromptTemplate and RagTemplate for customizable prompt generation.
-   Added LLMInterface with implementation for OpenAI LLM.
-   Updated project configuration to support multiple Python versions (3.8 to 3.12) in CI workflows.
-   Improved developer experience by copying the docstring from the `Retriever.get_search_results` method to the `Retriever.search` method
-   Support for specifying database names in index handling methods and retrievers.
-   User Guide in documentation.
-   Introduced result_formatter argument to all retrievers, allowing custom formatting of retriever results.

### Changed
-   Refactored import paths for retrievers to neo4j_genai.retrievers.
-   Implemented exception chaining for all re-raised exceptions to improve stack trace readability.
-   Made error messages in `index.py` more consistent.
-   Renamed `Retriever._get_search_results` to `Retriever.get_search_results`
-   Updated retrievers and index handling methods to accept optional database names.

## 0.2.0

### Fixed

-   Removed Pinecone and Weaviate retrievers from **init**.py to prevent ImportError when optional dependencies are not installed.
-   Moved few-shot examples in `Text2CypherRetriever` to the constructor for better initialization and usage. Updated unit tests and example script accordingly.
-   Fixed regex warnings in E2E tests for Weaviate and Pinecone retrievers.
-   Corrected HuggingFaceEmbeddings import in E2E tests.


## 0.2.0a5

## 0.2.0a3

### Added

-   Introduced custom exceptions for improved error handling, including `RetrieverInitializationError`, `SearchValidationError`, `FilterValidationError`, `EmbeddingRequiredError`, `RecordCreationError`, `Neo4jIndexError`, and `Neo4jVersionError`.
-   Retrievers that integrates with a Weaviate vector database: `WeaviateNeo4jRetriever`.
-   New return types that help with getting retriever results: `RetrieverResult` and `RetrieverResultItem`.
-   Supported wrapper embedder object for sentence-transformers embeddings: `SentenceTransformerEmbeddings`.
-   `Text2CypherRetriever` object which allows for the retrieval of records from a Neo4j database using natural language.

### Changed

-   Replaced `ValueError` with custom exceptions across various modules for clearer and more specific error messages.

### Fixed

-   Updated documentation to include new custom exceptions.
-   Improved the use of Pydantic for input data validation for retriever objects.
