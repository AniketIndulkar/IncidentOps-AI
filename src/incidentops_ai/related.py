"""Find earlier incidents related to a new one, by letting the model look them up with a tool."""

from incidentops_ai.llm.prompts import load_prompt
from incidentops_ai.llm.provider import LLMProvider, ModelConfig
from incidentops_ai.llm.tool_loop import ToolLoopResult, run_tool_loop
from incidentops_ai.tools.registry import ToolRegistry

RELATED_PROMPT = "related_incidents"
RELATED_PROMPT_VERSION = "v1"


async def find_related_incidents(
    provider: LLMProvider, registry: ToolRegistry, text: str, config: ModelConfig
) -> ToolLoopResult:
    prompt = load_prompt(RELATED_PROMPT, RELATED_PROMPT_VERSION, input_text=text)
    return await run_tool_loop(provider, prompt, registry, config)
