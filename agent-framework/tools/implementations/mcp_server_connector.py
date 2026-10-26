"""Client-side bridge to an independently running MCP server."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any

from src.exceptions import ToolError
from tools.base import BaseTool


class McpServerConnectorTool(BaseTool):
    """Connect to an external MCP server and invoke one MCP operation.

    The framework never starts or owns the MCP server. The server must already
    be running independently and its endpoint is supplied through
    ``MCP_SERVER_URL`` or the optional ``server_url`` argument.
    """

    DEFAULT_BLOCKED_OPERATIONS: set[str] = set()
    _cached_summary: str | None = None
    _cached_time: float = 0.0

    @classmethod
    def get_operations_summary(cls, endpoint: str) -> str:
        """Fetch and cache a summary of available operations from the MCP server."""
        now = time.time()
        if cls._cached_summary and (now - cls._cached_time < 300):
            return cls._cached_summary

        async def _fetch():
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client

            async with streamablehttp_client(endpoint) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    res = await session.list_tools()
                    groups: dict[str, list[str]] = {}
                    for t in res.tools:
                        prefix = t.name.split("_")[0] if "_" in t.name else "general"
                        groups.setdefault(prefix, []).append(t.name)
                    lines = []
                    for prefix, names in groups.items():
                        lines.append(f"  - {prefix}: {', '.join(names)}")
                    return "\n".join(lines)

        try:
            summary = cls._run_async(_fetch())
            cls._cached_summary = summary
            cls._cached_time = now
            return summary
        except Exception:
            return ""

    def get_description(self) -> str:
        """Dynamically enhance the tool description with operations available on the server."""
        base_desc = (
            "Connects the agent to an independently running MCP server over Streamable HTTP "
            "and invokes one discovered MCP operation."
        )
        endpoint = os.getenv("MCP_SERVER_URL", "").strip()
        if not endpoint:
            return base_desc

        summary = self.get_operations_summary(endpoint)
        if summary:
            return (
                f"{base_desc}\n\n"
                f"Available operations on connected MCP server:\n{summary}\n\n"
                f"Invoke operations directly with standard arguments. Only pass operation: 'list_tools' if exploring an unfamiliar server."
            )
        return base_desc

    @classmethod
    def _normalize_arguments(cls, arguments: Any) -> dict[str, Any]:
        """Generic normalization of arguments across different MCP conventions (database, files, and APIs)."""
        if not arguments:
            return {}

        if isinstance(arguments, str):
            s = arguments.strip()
            if s.startswith("{") and s.endswith("}"):
                try:
                    arguments = json.loads(s)
                except Exception:
                    arguments = {"path": s}
            else:
                arguments = {"path": s}

        if not isinstance(arguments, dict):
            try:
                args = dict(arguments)
            except Exception:
                args = {"path": str(arguments)}
        else:
            args = dict(arguments)

        # Identify explicit source and destination if both are provided under aliases
        src_candidate = None
        for s_alias in ("source_path", "source", "src", "original_path", "input_path", "from_path"):
            if s_alias in args and args[s_alias]:
                src_candidate = args[s_alias]
                break

        dst_candidate = None
        for d_alias in ("output_path", "destination_path", "dest_path", "out_path", "out_file", "output", "target_path", "to_path"):
            if d_alias in args and args[d_alias]:
                dst_candidate = args[d_alias]
                break

        if src_candidate and dst_candidate:
            args["source_path"] = str(src_candidate).strip().strip('"').strip("'")
            args["path"] = str(dst_candidate).strip().strip('"').strip("'")
        else:
            # File path aliases (Word, PDF, PPTX, Image, plain text, and filesystem tools)
            for alias in (
                "file_path",
                "filepath",
                "file",
                "filename",
                "document_path",
                "doc_path",
                "image_path",
                "img_path",
                "ppt_path",
                "pdf_path",
                "source_path",
                "src",
                "source",
                "target_path",
            ):
                if alias in args and "path" not in args:
                    args["path"] = args[alias]

            # Destination / output path aliases
            if dst_candidate and "output_path" not in args:
                args["output_path"] = dst_candidate

            # For operations requiring both source and destination (e.g., pptx_modify):
            # If output_path is specified, map path to output_path and preserve source_path.
            if "output_path" in args:
                if "source_path" not in args and "path" in args:
                    args["source_path"] = args["path"]
                args["path"] = args["output_path"]
            else:
                if "source_path" in args and "path" not in args:
                    args["path"] = args["source_path"]
                elif "path" in args and "source_path" not in args:
                    args["source_path"] = args["path"]

        # Clean string paths if present
        if "path" in args and isinstance(args["path"], str):
            args["path"] = args["path"].strip().strip('"').strip("'")
        if "source_path" in args and isinstance(args["source_path"], str):
            args["source_path"] = args["source_path"].strip().strip('"').strip("'")
        if "output_path" in args and isinstance(args["output_path"], str):
            args["output_path"] = args["output_path"].strip().strip('"').strip("'")

        # Date and footer aliases for presentations and documents
        for alias in ("date", "footer_date", "footer", "current_date", "system_date", "date_text"):
            if alias in args and "date_all_slides" not in args:
                args["date_all_slides"] = args[alias]

        if "footer" in args and isinstance(args["footer"], bool):
            args.setdefault("date_as_footer", args["footer"])
        elif any(k in args for k in ("footer", "date_as_footer", "footer_date")):
            args.setdefault("date_as_footer", True)

        # Table aliases
        if "table_name" in args and "table" not in args:
            args["table"] = args["table_name"]
        elif "table" in args and "table_name" not in args:
            args["table_name"] = args["table"]

        # Filter / WHERE aliases
        for alias in ("where_clause", "filters", "filter", "query_criteria", "criteria"):
            if alias in args and "where" not in args:
                args["where"] = args[alias]

        # SQL / Query aliases
        for alias in ("sql", "statement", "sql_query"):
            if alias in args and "query" not in args:
                args["query"] = args[alias]

        # Limit coercion
        if "limit" in args:
            try:
                args["limit"] = int(args["limit"])
            except (ValueError, TypeError):
                pass

        # String 'where' to dictionary coercion
        if "where" in args and isinstance(args["where"], str):
            w_str = args["where"].strip()
            if w_str.startswith("{") and w_str.endswith("}"):
                try:
                    args["where"] = json.loads(w_str)
                except Exception:
                    pass
            if isinstance(args["where"], str):
                parsed = {}
                clauses = re.split(r"\s+and\s+", w_str, flags=re.IGNORECASE)
                valid = True
                for clause in clauses:
                    m = re.match(r"^([a-zA-Z0-9_]+)\s*=\s*(.*?)$", clause.strip())
                    if m:
                        col = m.group(1).strip()
                        val = m.group(2).strip("'\" ")
                        if val.lower() == "true":
                            parsed[col] = True
                        elif val.lower() == "false":
                            parsed[col] = False
                        elif val.isdigit():
                            parsed[col] = int(val)
                        else:
                            parsed[col] = val
                    else:
                        valid = False
                        break
                if valid and parsed:
                    args["where"] = parsed

        return args

    def execute(
        self,
        operation: str,
        arguments: dict[str, Any] | None = None,
        server_url: str | None = None,
    ) -> Any:
        """Invoke a read-only operation on the external MCP server."""
        operation = (operation or "").strip()
        if not operation:
            raise ToolError(
                "MCP operation is required.",
                user_message="MCP operation is required.",
                tool_name="mcp_server_connector",
            )

        env_endpoint = os.getenv("MCP_SERVER_URL", "").strip()
        endpoint = env_endpoint
        if not endpoint:
            raise ToolError(
                "MCP_SERVER_URL is not configured.",
                user_message="Configure MCP_SERVER_URL before using the MCP Server Connector.",
                tool_name="mcp_server_connector",
            )

        blocked = set(self.DEFAULT_BLOCKED_OPERATIONS)
        configured_blocked = os.getenv("MCP_BLOCKED_OPERATIONS", "")
        if configured_blocked.strip():
            blocked = {item.strip() for item in configured_blocked.split(",") if item.strip()}

        if operation in blocked:
            raise ToolError(
                f"MCP operation '{operation}' is blocked by the framework MCP safety policy.",
                user_message=(
                    "The requested MCP operation is blocked by the current safety policy."
                ),
                tool_name="mcp_server_connector",
            )

        payload = self._normalize_arguments(arguments)

        try:
            return self._run_async(
                self._call_mcp(endpoint, operation, payload)
            )
        except ToolError:
            raise
        except Exception as exc:
            err_msg = str(exc)
            if hasattr(exc, "exceptions") and exc.exceptions:
                err_msg = "; ".join(str(e) for e in exc.exceptions)
            raise ToolError(
                f"MCP server call failed: {err_msg}",
                user_message="The external MCP server could not complete the requested operation.",
                tool_name="mcp_server_connector",
                tool_input={"operation": operation},
            ) from exc

    @staticmethod
    def _run_async(coro):
        """Run an async MCP call from the framework's synchronous tool contract."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()

    @classmethod
    async def _call_mcp(
        cls,
        endpoint: str,
        operation: str,
        arguments: dict[str, Any],
    ) -> Any:
        """Open an MCP Streamable HTTP session, call one operation, and close it."""
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client
        except ImportError as exc:
            raise ToolError(
                "The MCP Python client dependency is not installed.",
                user_message="Install the framework requirements to enable the MCP Server Connector.",
                tool_name="mcp_server_connector",
            ) from exc

        async with streamablehttp_client(endpoint) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()

                if operation == "list_tools":
                    result = await session.list_tools()
                    tools_list = []
                    for tool in result.tools:
                        properties = (
                            tool.inputSchema.get("properties", {})
                            if hasattr(tool, "inputSchema") and isinstance(tool.inputSchema, dict)
                            else {}
                        )
                        required = (
                            tool.inputSchema.get("required", [])
                            if hasattr(tool, "inputSchema") and isinstance(tool.inputSchema, dict)
                            else []
                        )
                        param_summary = {
                            k: f"{v.get('type', 'any')}{' (required)' if k in required else ''}"
                            for k, v in properties.items()
                        }
                        tools_list.append(
                            {
                                "name": tool.name,
                                "description": tool.description,
                                "parameters": param_summary,
                            }
                        )
                    return tools_list

                matched = None
                result = await session.call_tool(
                    operation,
                    arguments=arguments,
                )

                # Check if operation was unknown
                is_unknown = False
                if result.isError and result.content:
                    first_text = getattr(result.content[0], "text", "")
                    if "Unknown tool" in first_text:
                        is_unknown = True

                if is_unknown:
                    tools_res = await session.list_tools()
                    available_names = [t.name for t in tools_res.tools]
                    op_lower = operation.strip().lower()

                    matched = None
                    # 1. Exact case-insensitive match
                    for name in available_names:
                        if name.lower() == op_lower:
                            matched = name
                            break

                    # 2. Suffix match (e.g. 'select_rows' -> 'pg_select_rows')
                    if not matched:
                        for name in available_names:
                            if name.lower().endswith(f"_{op_lower}"):
                                matched = name
                                break

                    # 3. Semantic database/table operation matching
                    if not matched and ("table" in arguments or "table_name" in arguments):
                        if any(k in op_lower for k in ("select", "read", "fetch", "query_records", "get_rows")):
                            for name in available_names:
                                if name.endswith("_select_rows"):
                                    matched = name
                                    break
                        elif any(k in op_lower for k in ("describe", "schema", "columns")):
                            for name in available_names:
                                if name.endswith("_describe_table"):
                                    matched = name
                                    break
                        elif any(k in op_lower for k in ("list", "tables")):
                            for name in available_names:
                                if name.endswith("_list_tables"):
                                    matched = name
                                    break

                    if not matched and ("query" in arguments or "sql" in arguments):
                        for name in available_names:
                            if name.endswith("_execute_query"):
                                matched = name
                                break

                    # 4. Semantic document, PDF, Word, PowerPoint, Excel, CSV, Image matching
                    if not matched:
                        # PDF operations
                        if "pdf" in op_lower:
                            if any(k in op_lower for k in ("info", "meta", "page_count", "count")):
                                matched = "pdf_get_info" if "pdf_get_info" in available_names else None
                            elif any(k in op_lower for k in ("create", "write", "make", "new")):
                                matched = "pdf_create" if "pdf_create" in available_names else None
                            elif any(k in op_lower for k in ("append", "add_page")):
                                matched = "pdf_append_page" if "pdf_append_page" in available_names else None
                            elif any(k in op_lower for k in ("read", "fetch", "get", "text", "extract")):
                                matched = "pdf_read_text" if "pdf_read_text" in available_names else None
                            else:
                                matched = "pdf_read_text" if "pdf_read_text" in available_names else None

                        # Word / DOCX operations
                        elif any(k in op_lower for k in ("docx", "word", "doc")):
                            if any(k in op_lower for k in ("create", "new", "write")):
                                matched = "docx_create" if "docx_create" in available_names else None
                            elif any(k in op_lower for k in ("append", "add_paragraph")):
                                matched = "docx_append_paragraph" if "docx_append_paragraph" in available_names else None
                            elif any(k in op_lower for k in ("replace", "substitute")):
                                matched = "docx_replace_text" if "docx_replace_text" in available_names else None
                            elif any(k in op_lower for k in ("read", "fetch", "get", "text", "extract")):
                                matched = "docx_read" if "docx_read" in available_names else None
                            else:
                                matched = "docx_read" if "docx_read" in available_names else None

                        # PowerPoint / PPTX operations
                        elif any(k in op_lower for k in ("pptx", "ppt", "powerpoint", "slide", "deck", "presentation")):
                            if any(k in op_lower for k in ("modify", "update", "edit", "change", "replace")):
                                matched = "pptx_modify" if "pptx_modify" in available_names else None
                            elif any(k in op_lower for k in ("add_slide", "new_slide", "insert_slide")):
                                matched = "pptx_add_slide" if "pptx_add_slide" in available_names else None
                            elif any(k in op_lower for k in ("create", "new", "write", "generate")):
                                matched = "pptx_create" if "pptx_create" in available_names else None
                            elif any(k in op_lower for k in ("read", "fetch", "get", "text", "extract")):
                                matched = "pptx_read" if "pptx_read" in available_names else None
                            else:
                                matched = "pptx_read" if "pptx_read" in available_names else None

                        # Image / OCR operations
                        elif any(k in op_lower for k in ("image", "img", "ocr", "photo", "picture", "vision")):
                            matched = "image_analyze" if "image_analyze" in available_names else None

                        # Excel / Spreadsheet operations
                        elif any(k in op_lower for k in ("xlsx", "excel", "sheet", "spreadsheet")):
                            if any(k in op_lower for k in ("create", "write", "new")):
                                matched = "xlsx_create" if "xlsx_create" in available_names else None
                            elif any(k in op_lower for k in ("update", "cell")):
                                matched = "xlsx_update_cell" if "xlsx_update_cell" in available_names else None
                            elif any(k in op_lower for k in ("append", "row")):
                                matched = "xlsx_append_row" if "xlsx_append_row" in available_names else None
                            else:
                                matched = "xlsx_read" if "xlsx_read" in available_names else None

                        # CSV operations
                        elif "csv" in op_lower:
                            if any(k in op_lower for k in ("write", "create", "new")):
                                matched = "csv_write" if "csv_write" in available_names else None
                            elif any(k in op_lower for k in ("append", "row")):
                                matched = "csv_append_row" if "csv_append_row" in available_names else None
                            elif any(k in op_lower for k in ("to_xlsx", "convert")):
                                matched = "csv_to_xlsx" if "csv_to_xlsx" in available_names else None
                            else:
                                matched = "csv_read" if "csv_read" in available_names else None

                        # Plain text / Markdown operations
                        elif any(k in op_lower for k in ("text_read", "read_text", "read_file")):
                            matched = "text_read" if "text_read" in available_names else None
                        elif any(k in op_lower for k in ("text_write", "write_text", "write_file")):
                            matched = "text_write" if "text_write" in available_names else None

                    if matched:
                        result = await session.call_tool(
                            matched,
                            arguments=arguments,
                        )
                    else:
                        return {
                            "error": f"Unknown tool '{operation}'.",
                            "available_operations": available_names,
                            "hint": "Please select from the available operations on this MCP server or invoke 'list_tools'.",
                        }

                # Check for column does not exist error to enrich response with schema
                if result.isError and result.content:
                    err_text = getattr(result.content[0], "text", "")
                    m_col = re.search(r'column "([^"]+)" does not exist', err_text, re.IGNORECASE)
                    if m_col:
                        missing_col = m_col.group(1)
                        target_table = arguments.get("table") or arguments.get("table_name")
                        if not target_table and "query" in arguments:
                            m_tbl = re.search(r'\bfrom\s+([a-zA-Z0-9_\.]+)', str(arguments["query"]), re.IGNORECASE)
                            if m_tbl:
                                target_table = m_tbl.group(1).split(".")[-1]

                        col_names = []
                        if target_table:
                            try:
                                desc_res = await session.call_tool("pg_describe_table", arguments={"table": target_table})
                                if not desc_res.isError:
                                    for b in desc_res.content:
                                        if getattr(b, "type", None) == "text":
                                            try:
                                                c_data = json.loads(b.text)
                                                if isinstance(c_data, dict) and "column_name" in c_data:
                                                    col_names.append(c_data["column_name"])
                                            except Exception:
                                                pass
                            except Exception:
                                pass

                        import difflib
                        close_col = difflib.get_close_matches(missing_col, col_names, n=1, cutoff=0.4) if col_names else []
                        suggestion = f" Did you mean '{close_col[0]}'?" if close_col else ""
                        return {
                            "error": f"Column '{missing_col}' does not exist in table '{target_table or 'specified'}'.",
                            "available_columns": col_names if col_names else "Could not inspect columns.",
                            "hint": (
                                f"The table '{target_table}' has columns: {col_names}.{suggestion} Please use one of the available columns."
                                if col_names
                                else "Please inspect columns using 'pg_describe_table'."
                            ),
                        }

                    # Check for relation / table does not exist error to enrich response
                    m_rel = re.search(r'relation "([^"]+)" does not exist', err_text, re.IGNORECASE) or re.search(r'table "([^"]+)" does not exist', err_text, re.IGNORECASE)
                    if m_rel:
                        missing_table = m_rel.group(1).split(".")[-1]
                        avail_tables: list[str] = []
                        try:
                            tbl_res = await session.call_tool("pg_list_tables", arguments={})
                            if not tbl_res.isError:
                                for b in tbl_res.content:
                                    if getattr(b, "type", None) == "text":
                                        try:
                                            parsed_t = json.loads(b.text)
                                            if isinstance(parsed_t, list):
                                                avail_tables.extend([str(t) for t in parsed_t])
                                            elif isinstance(parsed_t, str):
                                                avail_tables.append(parsed_t)
                                        except Exception:
                                            avail_tables.append(b.text)
                        except Exception:
                            pass

                        # Generic table resolution without hardcoded names
                        candidate_table = None
                        if avail_tables:
                            # 1. Exact case-insensitive match
                            for t in avail_tables:
                                if t.lower() == missing_table.lower():
                                    candidate_table = t
                                    break

                            # 2. Token overlap / substring match
                            if not candidate_table:
                                m_tokens = set(re.findall(r"[a-zA-Z0-9]+", missing_table.lower()))
                                best_score = 0
                                for t in avail_tables:
                                    t_tokens = set(re.findall(r"[a-zA-Z0-9]+", t.lower()))
                                    overlap = len(m_tokens.intersection(t_tokens))
                                    if overlap > best_score:
                                        best_score = overlap
                                        candidate_table = t

                            # 3. Fuzzy similarity via difflib
                            if not candidate_table:
                                import difflib
                                close = difflib.get_close_matches(missing_table.lower(), [t.lower() for t in avail_tables], n=1, cutoff=0.4)
                                if close:
                                    for t in avail_tables:
                                        if t.lower() == close[0]:
                                            candidate_table = t
                                            break

                            # 4. Single available table fallback
                            if not candidate_table and len(avail_tables) == 1:
                                candidate_table = avail_tables[0]

                        if candidate_table and candidate_table != missing_table:
                            if "table" in arguments:
                                arguments["table"] = candidate_table
                            if "table_name" in arguments:
                                arguments["table_name"] = candidate_table
                            retry_res = await session.call_tool(matched or operation, arguments=arguments)
                            if not retry_res.isError:
                                result = retry_res
                            else:
                                return {
                                    "error": f"Table '{missing_table}' does not exist in the database.",
                                    "available_tables": avail_tables,
                                    "hint": f"The table '{missing_table}' was not found. Available database tables: {avail_tables}. Please query one of the available tables.",
                                }
                        else:
                            return {
                                "error": f"Table '{missing_table}' does not exist in the database.",
                                "available_tables": avail_tables,
                                "hint": f"The table '{missing_table}' was not found. Available database tables: {avail_tables}. Please query one of the available tables.",
                            }

                output: list[Any] = []
                for block in result.content:
                    if getattr(block, "type", None) == "text":
                        output.append(block.text)
                    else:
                        output.append(
                            {
                                "content_type": getattr(block, "type", "unknown")
                            }
                        )

                parsed_output: list[Any] = []
                for item in output:
                    if isinstance(item, str):
                        try:
                            parsed_output.append(json.loads(item))
                        except json.JSONDecodeError:
                            parsed_output.append(item)
                    else:
                        parsed_output.append(item)

                # Generic fallback: If select_rows returns 0 rows and a string filter was provided,
                # attempt case-insensitive prefix match (e.g. value 'BLR' matches 'BLR-01')
                is_empty_res = (
                    parsed_output == []
                    or parsed_output == [[]]
                    or (len(parsed_output) == 1 and parsed_output[0] == [])
                )
                if (
                    is_empty_res
                    and any(operation.endswith(suffix) for suffix in ("_select_rows", "select_rows"))
                ):
                    where_conds = arguments.get("where")
                    target_table = arguments.get("table") or arguments.get("table_name")
                    if isinstance(where_conds, dict) and target_table:
                        prefix_clauses = []
                        for col, val in where_conds.items():
                            if isinstance(val, str) and "%" not in val:
                                safe_val = val.replace("'", "''")
                                prefix_clauses.append(f'"{col}" ILIKE \'{safe_val}%\'')
                            elif isinstance(val, (int, float)):
                                prefix_clauses.append(f'"{col}" = {val}')
                        if prefix_clauses:
                            limit_val = arguments.get("limit", 10)
                            fallback_sql = f'SELECT * FROM "{target_table}" WHERE {" AND ".join(prefix_clauses)} LIMIT {limit_val};'
                            try:
                                fb_res = await session.call_tool("pg_execute_query", arguments={"query": fallback_sql})
                                if not fb_res.isError and fb_res.content:
                                    fb_rows = []
                                    for b in fb_res.content:
                                        if getattr(b, "type", None) == "text":
                                            try:
                                                fb_rows.append(json.loads(b.text))
                                            except Exception:
                                                fb_rows.append(b.text)
                                    if fb_rows:
                                        return fb_rows
                            except Exception:
                                pass

                if len(parsed_output) == 1:
                    return parsed_output[0]
                return parsed_output
