# Project: LMS Student Course Information Chatbot

## 1. Project Objective

Build a locally hosted AI chatbot that answers student questions about available courses using information extracted from PDF documents.

The chatbot will eventually be integrated into an existing Learning Management System (LMS).

There is no requirement to develop a frontend. The application should expose REST API endpoints that the existing LMS can consume.

The chatbot must use Retrieval-Augmented Generation (RAG) to retrieve relevant information from course PDFs and generate answers using a local LLM.

## 2. Development Environment

- Operating System: Windows
- IDE: Antigravity
- Programming Language: Python
- Processor: Intel Core i3
- RAM: 12 GB
- GPU: Intel UHD Graphics (integrated graphics)
- LLM Runtime: Ollama
- Installed LLM: Qwen3 4B

Important hardware constraints:
- Optimise the application for CPU-based inference.
- Avoid unnecessarily large models and context windows.
- Avoid multiple simultaneous LLM requests.
- Keep memory usage low.
- Do not assume dedicated NVIDIA GPU availability.
- Verify the actual installed Ollama model tag using `ollama list` rather than assuming a model name.

## 3. Existing Course Documents

All original PDFs are located in:

`LMS_ChatBot/Courses/`

The five available PDFs are:

1. Java_Masterclass.pdf
2. Ethical_Hacking.pdf
3. Machine_Learning.pdf
4. The_Complete_Python_Bootcamp.pdf
5. AWS_Certified_Cloud.pdf

These documents contain course information, descriptions, curriculum, instructors, course duration, requirements and other available course details.

Important: These are saved course webpage PDFs, not necessarily complete course teaching materials.

The chatbot must only answer using information supported by the available documents.

Do not invent missing information or use general LLM knowledge as a substitute for missing course information.

## 4. Technology Stack

Use the following technologies:

- Python: Core development.
- PyMuPDF: PDF text extraction.
- Custom Python: Text cleaning and chunking.
- Sentence Transformers: Embedding generation.
- Embedding model: `sentence-transformers/all-MiniLM-L6-v2`
- ChromaDB: Persistent vector database.
- Ollama: Local LLM serving.
- Qwen3 4B: Answer generation.
- FastAPI: Backend API.
- Uvicorn: API server.

Initially implement RAG using custom Python instead of LangChain.

Keep the architecture modular so a framework can be introduced later if necessary.

## 5. Target Folder Structure

LMS_ChatBot/

- Courses/
- data/
  - extracted_text/
  - processed/
- app/
  - ingestion/
    - pdf_loader.py
    - text_cleaner.py
    - chunker.py
    - indexer.py
  - retrieval/
    - embeddings.py
    - retriever.py
  - generation/
    - llm.py
    - prompts.py
  - api/
    - routes.py
  - config.py
  - main.py
- vector_db/
  - chroma/
- evaluation/
  - test_questions.json
  - evaluate.py
- requirements.txt
- .env
- .gitignore
- README.md

## 6. Implementation Phases

### Phase 1: PDF Inspection and Text Extraction

This is the immediate task.

Requirements:

1. Read all PDFs from the Courses folder automatically.
2. Extract text page by page using PyMuPDF.
3. Save extracted text into individual TXT files under data/extracted_text/.
4. Preserve page numbers.
5. Print document name, page count and extracted character count.
6. Detect pages with little or no extractable text.
7. Do not modify the original PDF files.

Example:

The_Complete_Python_Bootcamp.pdf should produce:

data/extracted_text/Python_Bootcamp.txt

Use clear page separators:

--- PAGE 1 ---
Extracted text...

--- PAGE 2 ---
Extracted text...

After processing all documents, generate an extraction report.

Important:
- Do not remove course information.
- Identify repeated headers, footers and promotional content.
- Inspect the extraction quality before deciding cleaning rules.
- Handle extraction errors gracefully.

Deliverables:
- pdf_loader.py
- Five TXT files.
- Extraction report.

Stop after this phase and request approval before continuing.

### Phase 2: Text Cleaning and Chunking

After reviewing extracted TXT files:

1. Remove repetitive headers and footers.
2. Remove irrelevant repeated promotional content where appropriate.
3. Preserve useful course information.
4. Preserve course headings and curriculum structure.
5. Split text into meaningful chunks.
6. Keep course name, PDF name, page number and chunk ID as metadata.

Initial chunking configuration:

- Chunk size: approximately 500 tokens.
- Chunk overlap: approximately 75 tokens.

These values should be configurable.

Avoid splitting course descriptions, requirements and curriculum sections unnecessarily.

Deliverables:
- text_cleaner.py
- chunker.py
- processed/chunks.json

### Phase 3: Embeddings and Vector Database

1. Load all-MiniLM-L6-v2 locally.
2. Generate embeddings for processed chunks.
3. Store vectors in ChromaDB.
4. Store original text and metadata.
5. Use persistent local storage under vector_db/chroma/.
6. Avoid generating duplicate records when the same documents are indexed again.
7. Support updating and rebuilding the index.

Deliverables:
- embeddings.py
- indexer.py
- Persistent ChromaDB collection.

### Phase 4: Retrieval System

Implement semantic similarity search.

When a student asks a question:

1. Convert the question into an embedding.
2. Search ChromaDB.
3. Retrieve the most relevant chunks.
4. Return document name, page number and similarity information.

Initial top_k: 4.

Provide a command-line testing interface to inspect retrieved chunks without invoking the LLM.

Important:
- Test retrieval accuracy before LLM integration.
- Avoid returning irrelevant chunks.
- Preserve document metadata.
- Support course-specific filtering when required.

Deliverable:
- retriever.py
- Retrieval testing script.

### Phase 5: LLM Integration and RAG

Connect Ollama to the retrieval system using its Python client.

Use the installed Qwen3 4B model.

Requirements:

1. Retrieve relevant chunks for each question.
2. Pass only the relevant context to the LLM.
3. Use a controlled system prompt.
4. Generate clear English responses.
5. Include source document and page references.
6. Handle missing information correctly.
7. Support conversational follow-up questions.

System prompt requirements:

"You are a Student Course Information Assistant.

Answer student questions using only the supplied course context.

Do not invent course details, fees, prerequisites, certifications, instructors or durations.

If the provided context does not contain the requested information, clearly state that the information is not available in the provided course documents.

When comparing courses, use only information available for both courses.

Provide concise and helpful responses.

Mention the source document and page number whenever possible."

Do not allow the model to answer unrelated general-knowledge questions as if they were course information.

Deliverables:
- llm.py
- prompts.py
- Working command-line chatbot.

### Phase 6: FastAPI Backend

Create the following endpoints:

POST /api/v1/chat

GET /api/v1/health

POST /api/v1/ingest

Chat endpoint request:

{
  "student_id": "STU101",
  "session_id": "SESSION001",
  "message": "What is the duration of the Python course?"
}

Example response:

{
  "answer": "The course duration is 22 hours and 23 minutes.",
  "sources": [
    {
      "document": "The_Complete_Python_Bootcamp.pdf",
      "page": 2
    }
  ],
  "session_id": "SESSION001"
}

The exact answer must be based on the retrieved PDF content, not hardcoded.

Implement basic session-based conversation history.

Protect document ingestion endpoints so ordinary students cannot modify the knowledge base.

Deliverables:
- routes.py
- main.py
- API documentation.

### Phase 7: Testing and Evaluation

Prepare questions covering:

- Course duration.
- Course requirements.
- Course instructor.
- Course curriculum.
- Course ratings.
- Course comparisons.
- Missing course information.
- Follow-up questions.
- Questions unrelated to available courses.

Measure:

- Retrieval relevance.
- Answer correctness.
- Groundedness.
- Source accuracy.
- Response latency.

Verify that the chatbot does not hallucinate unsupported information.

## 7. Development Rules

Follow these rules throughout implementation:

1. Work phase by phase.
2. Do not generate the entire application in one step.
3. Explain the purpose of every major module.
4. Keep code modular and maintainable.
5. Use relative paths or configurable project-root paths.
6. Do not modify original PDFs.
7. Do not hardcode course information.
8. Do not introduce unnecessary libraries.
9. Keep model and database configurations in config.py or environment variables.
10. Test every module before moving to the next phase.
11. Maintain a README with setup and execution instructions.
12. Use appropriate error handling and logging.

Most importantly, do not move to the next phase until the current phase has been tested successfully and reviewed.

## 8. Immediate Instruction

Start with Phase 1 only.

Inspect the existing project directory and the five PDFs.

Create the PDF extraction module, export individual TXT files and generate an extraction report.

Do not start chunking, embeddings, ChromaDB or LLM integration yet.

After completing Phase 1, show:
- Files created.
- Page counts.
- Extracted character counts.
- Pages with little or no text.
- Any extraction issues.
- A sample of extracted content from each document.

Wait for approval before proceeding to Phase 2.