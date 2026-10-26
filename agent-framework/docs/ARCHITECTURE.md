# Enterprise Agent Framework: Architecture & Technical Design

## 1. System Vision & Overview

The **Enterprise Agent Framework (v7)** is an enterprise-grade agentic development and orchestration platform. It allows engineers to define, execute, monitor, and govern AI agents, workflows, and tools via an interactive Command Line Interface (CLI) backed by CrewAI and Model Context Protocol (FastMCP) microservices.

### Decoupled Dual-Tier Architecture

To achieve enterprise isolation, security, and scalability, the architecture is split into two independent domains:

1. **Cognitive & Orchestration Tier (`agent-framework`)**:
   - Manages agent personas, goals, prompt templates, and execution planning.
   - Provides deterministic input/output guardrails.
   - Hosts the RAG subsystem (ChromaDB vector store) for domain-grounded reasoning.
   - Interacts with LLM providers (Gemini, OpenAI, Anthropic, Groq, Ollama) via LiteLLM abstraction.
   - Operates strictly as an **MCP Client** over Streamable HTTP (`MCP_SERVER_URL`).

2. **Integration & Gateway Tier (`mcp-server2`)**:
   - An independent FastMCP server running over ASGI/Uvicorn (`server.py --serve --transport http --port 8000`).
   - Merges 12 enterprise connectors under a unified endpoint (`/mcp`):
     - **Database**: PostgreSQL (`postgresql.py`)
     - **Document & File Systems**: Word, PPTX, Excel, PDF, CSV, Text, Markdown (`files.py`)
     - **Project Management**: Jira Cloud/Data Center (`jira.py`)
     - **Knowledge Bases**: Confluence & Enterprise Wikis (`confluence.py`)
     - **Code Repositories**: GitHub Cloud & GitHub Enterprise (`github.py`, `github_enterprise.py`)
     - **Collaboration & Office 365**: SharePoint, OneDrive, Teams, Outlook (`sharepoint.py`, `onedrive.py`, `teams.py`, `outlook.py`)
     - **Quality Assurance**: TestRail (`testrail.py`)
     - **Computer Vision**: RapidOCR & Layout Extraction (`image.py`)

---

## 2. High-Level Architecture Diagram

```mermaid
flowchart TB
    subgraph UI ["CLI Control Plane"]
        CLI[main.py: Interactive Typer / Rich CLI]
        AgentMenu[Agent Management]
        WorkflowMenu[Workflow Management]
        ToolMenu[Tool Management]
        GuardrailMenu[Guardrail Management]
        KBMenu[Knowledge Base / RAG]
        SysStatus[System Status & Telemetry]
        
        CLI --> AgentMenu
        CLI --> WorkflowMenu
        CLI --> ToolMenu
        CLI --> GuardrailMenu
        CLI --> KBMenu
        CLI --> SysStatus
    end

    subgraph Runtime ["Agent Framework Runtime"]
        AppRuntime[CrewRuntime & Orchestrator]
        LLMResolver[LLM Resolver & Gateway]
        GuardrailEngine[Guardrail Evaluator<br/>Input / Output Hooks]
        ChromaStore[(ChromaDB Vector Store)]
        Logger[Two-Level Observability Engine<br/>Framework & Execution Logs]
        MCPConnector[McpServerConnectorTool]

        AgentMenu & WorkflowMenu --> AppRuntime
        AppRuntime --> LLMResolver
        AppRuntime --> GuardrailEngine
        AppRuntime --> ChromaStore
        AppRuntime --> Logger
        AppRuntime --> MCPConnector
    end

    subgraph Gateway ["MCP Gateway (mcp-server2)"]
        FastMCPEndpoint[FastMCP Server: /mcp<br/>ASGI / Uvicorn (Port 8000)]
        
        MCPConnector -->|Streamable HTTP / SSE| FastMCPEndpoint
        
        FastMCPEndpoint --> PGModule[PostgreSQL Asyncpg]
        FastMCPEndpoint --> FileModule[Files / Docs / PPTX / Excel]
        FastMCPEndpoint --> AtlassianModule[Jira & Confluence REST]
        FastMCPEndpoint --> GitModule[GitHub / GHE REST]
        FastMCPEndpoint --> M365Module[Azure AD / Graph API]
        FastMCPEndpoint --> TestRailModule[TestRail API]
        FastMCPEndpoint --> OCRModule[RapidOCR / ONNX]
    end

    subgraph External ["Enterprise Systems & APIs"]
        PGModule --> PostgreSQL[(PostgreSQL / Neon DB)]
        FileModule --> LocalFS[(Filesystem / Reports)]
        AtlassianModule --> JiraCloud[Jira / Confluence Cloud]
        GitModule --> GitHubRepos[GitHub / GHE Repos]
        M365Module --> MSGraph[SharePoint / OneDrive / Teams / Outlook]
        TestRailModule --> TestRailServer[TestRail Instance]
    end
```

---

## 3. Core Component Lifecycles

### A. Agent Execution Lifecycle
1. **Invocation**: User selects an agent from the CLI (`main.py` $\rightarrow$ `agent_menu`) or via direct command execution.
2. **Definition Loading**: The agent's YAML specification is read from `agents/definitions/<id>.yaml`.
3. **LLM Resolution**: The runtime resolves the model backend (`llm_resolver.py`), falling back from `EXECUTION_LLM_PROVIDER` to `LLM_PROVIDER`.
4. **Input Guardrails**: Any attached guardrails with phase `input` or `both` are executed against user input. If an action is `block` and validation fails, execution terminates immediately with a diagnostic message.
5. **Tool Binding**: Local tools (e.g. `knowledge_search`) and the `mcp_server_connector` are initialized. The MCP connector queries `MCP_SERVER_URL` for dynamically available tool schemas.
6. **CrewAI Execution**: The CrewAI `Agent` and `Task` are assembled and kicked off synchronously.
7. **Output Guardrails**: Post-execution outputs are evaluated against output guardrails.
8. **Logging & Persistence**: Execution events, tool invocations, tokens, and outputs are written to `logs/executions/agents/YYYY-MM-DD/<agent_name>.log` with all credentials automatically redacted.

### B. Workflow Lifecycle (Sequential & Hierarchical)
1. **Pipeline Instantiation**: Workflow definition loaded from `workflows/definitions/<id>.yaml`.
2. **Context Propagation**: A shared correlation `trace_id` is assigned across all steps in the workflow.
3. **Step Iteration**:
   - For sequential workflows: The output payload of Step $N$ is formatted and passed as input context to Step $N+1$.
   - For hierarchical workflows: A manager agent delegates tasks dynamically based on intermediate outcomes.
4. **Audit Trail**: Every nested agent, tool, and guardrail invocation records events tagged with the workflow's `trace_id`.

### C. RAG (Knowledge Base) Lifecycle
1. **Ingestion**: Documents (`.pdf`, `.docx`, `.md`, `.txt`, `.csv`, `.json`, `.yaml`) are dropped into `inputs/rag/` or selected via CLI.
2. **Validation**: Files are inspected for validity, non-empty content, and size constraints (`MAX_RAG_FILE_SIZE_MB`).
3. **Extraction & Chunking**: Parsers extract text and tabular structures, splitting content into semantic chunks with overlap.
4. **Vector Storage**: Embeddings are calculated and stored persistently inside ChromaDB (`data/chromadb/`).
5. **Retrieval**: Agents equipped with `knowledge_search` execute cosine similarity queries, receiving top-$K$ matching chunks accompanied by source filenames and metadata.

---

## 4. Observability & Security Architecture

### Two-Level Partitioned Logging
To prevent noisy, unmanageable monolith log files, logging is separated into:
- **Framework Log** (`logs/framework/YYYY-MM-DD/framework.log`): System events, menu choices, configuration loads.
- **Execution Log** (`logs/executions/<category>/YYYY-MM-DD/<entity>.log`): Dedicated log per concrete entity (`Agent1.log`, `Workflow1.log`, `Tool1.log`, etc.).

### Automatic Secret Redaction
Before any log statement reaches the disk:
- Strings matching known API key patterns (`AIza...`, `sk-...`, `ghp_...`, `Bearer ...`) are replaced with `[REDACTED]`.
- Key-value pairs with sensitive names (`password`, `token`, `secret`, `authorization`, `cookie`) have their values masked.

### Size-Capped Rotation
Every log file is capped at 10 MB (`EAF_LOG_MAX_FILE_BYTES = 10485760`) with a default backup retention count of 20 (`EAF_LOG_BACKUP_COUNT = 20`), ensuring disk usage remains controlled and predictable.

