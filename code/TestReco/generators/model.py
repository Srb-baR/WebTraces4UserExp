import dataclasses
import logging
import os
import re
import time
from typing import Dict, List, Literal, Optional, Union

import anthropic
import boto3
import openai
from dotenv import load_dotenv
from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.outputs import LLMResult
from langchain_community.chat_models import ChatAnthropic
from langchain_openai import ChatOpenAI
from langchain_ollama import ChatOllama  # Add this import
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_random_exponential

# Load environment variables from .env file
load_dotenv()

# Check if OpenRouter API key is available
openrouter_api_key = os.environ.get("OPENROUTER_API_KEY")
if openrouter_api_key:
    openrouter_client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=openrouter_api_key,
    )
else:
    openrouter_client = None
    print("Warning: OPENROUTER_API_KEY not found in environment variables")

# Global token usage tracking
completion_tokens = 0
prompt_tokens = 0

MessageRole = Literal["system", "user", "assistant"]


@dataclasses.dataclass()
class Message:
    role: MessageRole
    content: str


def message_to_str(message: Message) -> str:
    return f"{message.role}: {message.content}"


def messages_to_str(messages: List[Message]) -> str:
    return "\n".join([message_to_str(message) for message in messages])


def remove_unicode_chars(text: str) -> str:
    return re.sub(r"[^\x00-\x7F]+", "", text)


class TokenUsageCallbackHandler(BaseCallbackHandler):
    """Callback handler for tracking token usage."""

    def __init__(self):
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.request_prompt_tokens = 0
        self.request_completion_tokens = 0
        self.request_count = 0

    def on_llm_start(self, *args, **kwargs):
        """Reset request token counters when a new request starts."""
        self.request_prompt_tokens = 0
        self.request_completion_tokens = 0

    def on_llm_end(self, response: LLMResult, *args, **kwargs):
        """Update token usage when a request ends."""
        if hasattr(response, "llm_output") and response.llm_output:
            if "token_usage" in response.llm_output:
                usage = response.llm_output["token_usage"]
                self.request_prompt_tokens = usage.get("prompt_tokens", 0)
                self.request_completion_tokens = usage.get("completion_tokens", 0)
                self.total_prompt_tokens += self.request_prompt_tokens
                self.total_completion_tokens += self.request_completion_tokens
                self.request_count += 1

                # Only log token usage numbers without redundant headers
                logging.info(
                    f"Tokens: {self.request_prompt_tokens} prompt + {self.request_completion_tokens} completion = {self.request_prompt_tokens + self.request_completion_tokens} total"
                )

    def get_average_usage(self):
        """Get average token usage per request."""
        if self.request_count == 0:
            return 0, 0
        return (
            self.total_prompt_tokens / self.request_count,
            self.total_completion_tokens / self.request_count,
        )


# Create a global instance of the callback handler
token_usage_callback = TokenUsageCallbackHandler()


@retry(wait=wait_random_exponential(min=1, max=180), stop=stop_after_attempt(6))
def gpt_chat(
    model: str,
    messages: List[Message],
    max_tokens: int = 1024,
    temperature: float = 0.0,
    num_comps=1,
) -> Union[List[str], str]:
    global completion_tokens, prompt_tokens
    chat_model = ChatOpenAI(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        callbacks=[token_usage_callback],
    )
    response = chat_model(
        messages=[
            (
                SystemMessage(content=msg.content)
                if msg.role == "system"
                else HumanMessage(content=msg.content)
            )
            for msg in messages
        ]
    )
    completion_tokens += response.usage.completion_tokens
    prompt_tokens += response.usage.prompt_tokens
    return response.content


@retry(wait=wait_random_exponential(min=1, max=180), stop=stop_after_attempt(6))
def anthropic_chat(
    model: str,
    messages: List[Message],
    max_tokens: int = 1024,
    temperature: float = 0.0,
    num_comps=1,
) -> Union[List[str], str]:
    global completion_tokens, prompt_tokens
    chat_model = ChatAnthropic(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        callbacks=[token_usage_callback],
    )
    response = chat_model(
        messages=[
            (
                SystemMessage(content=msg.content)
                if msg.role == "system"
                else HumanMessage(content=msg.content)
            )
            for msg in messages
        ]
    )
    completion_tokens += response.usage.output_tokens
    prompt_tokens += response.usage.input_tokens
    return response.content


@retry(wait=wait_random_exponential(min=1, max=180), stop=stop_after_attempt(6))
def openrouter_chat(
    model: str,
    messages: List[Message],
    max_tokens: int = 1024,
    temperature: float = 0.0,
    num_comps=1,
) -> Union[List[str], str]:
    """Call models through OpenAI API (direct or OpenRouter fallback)."""
    global completion_tokens, prompt_tokens

    # Try direct OpenAI first if we have the key and it's an openai/ model
    openai_api_key = os.environ.get("OPENAI_API_KEY")
    if openai_api_key and model.startswith("openai/"):
        # Use direct OpenAI API
        model_name = model.replace("openai/", "")  # Remove openai/ prefix
        chat_model = ChatOpenAI(
            api_key=openai_api_key,
            model=model_name,
            max_tokens=max_tokens,
            temperature=temperature,
            callbacks=[token_usage_callback],
            timeout=120,  # 2 minute timeout
        )
    elif openrouter_api_key:
        # Fallback to OpenRouter
        chat_model = ChatOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=openrouter_api_key,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            callbacks=[token_usage_callback],
            timeout=120,  # 2 minute timeout for OpenRouter (free models can be slow)
        )
    else:
        raise ValueError(
            "No API key found. Please set OPENAI_API_KEY or OPENROUTER_API_KEY environment variable."
        )

    # Convert messages to LangChain format, handling PromptTemplate objects
    langchain_messages = []
    for msg in messages:
        content = msg.content
        if hasattr(content, "template"):  # If content is a PromptTemplate
            content = content.template
        if msg.role == "system":
            langchain_messages.append(SystemMessage(content=content))
        else:
            langchain_messages.append(HumanMessage(content=content))

    response = chat_model.invoke(langchain_messages)

    # Update token usage if available
    if hasattr(response, "usage") and response.usage:
        if hasattr(response.usage, "completion_tokens"):
            completion_tokens += response.usage.completion_tokens
        if hasattr(response.usage, "prompt_tokens"):
            prompt_tokens += response.usage.prompt_tokens

    return response.content


@retry(wait=wait_random_exponential(min=1, max=180), stop=stop_after_attempt(6))
def ollama_chat(
    model: str,
    messages: List[Message],
    max_tokens: int = 1024,
    temperature: float = 0.0,
    num_comps=1,
) -> Union[List[str], str]:
    """Call Ollama models locally."""
    global completion_tokens, prompt_tokens
    
    # Default Ollama base URL (can be overridden with OLLAMA_BASE_URL env var)
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    
    chat_model = ChatOllama(
        model=model,
        base_url=base_url,
        temperature=temperature,
        num_predict=max_tokens,  # Ollama uses num_predict instead of max_tokens
        callbacks=[token_usage_callback],
    )
    
    # Convert messages to LangChain format
    langchain_messages = []
    for msg in messages:
        content = msg.content
        if hasattr(content, "template"):  # If content is a PromptTemplate
            content = content.template
        if msg.role == "system":
            langchain_messages.append(SystemMessage(content=content))
        else:
            langchain_messages.append(HumanMessage(content=content))
    
    response = chat_model.invoke(langchain_messages)
    
    # Update token usage if available
    if hasattr(response, "usage") and response.usage:
        if hasattr(response.usage, "completion_tokens"):
            completion_tokens += response.usage.completion_tokens
        if hasattr(response.usage, "prompt_tokens"):
            prompt_tokens += response.usage.prompt_tokens
    
    return response.content


    def _bedrock_openai_api_key() -> str:
        return (
            os.environ.get("BEDROCK_OPENAI_API_KEY")
            or os.environ.get("BEDROCK_API_KEY")
            or os.environ.get("OPENAI_API_KEY", "")
        )


    def _bedrock_openai_base_url() -> str:
        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
        return os.environ.get(
            "BEDROCK_OPENAI_BASE_URL",
            f"https://bedrock-mantle.{region}.api.aws/openai/v1",
        ).rstrip("/")


    def _responses_output_text(response) -> str:
        output_text = getattr(response, "output_text", None)
        if output_text:
            return output_text

        fragments: List[str] = []
        for item in getattr(response, "output", []) or []:
            for content in getattr(item, "content", []) or []:
                text = getattr(content, "text", None) or getattr(content, "value", None)
                if text:
                    fragments.append(text)
        return "".join(fragments)


    @retry(wait=wait_random_exponential(min=1, max=180), stop=stop_after_attempt(6))
    def bedrock_openai_chat(
        model: str,
        messages: List[Message],
        max_tokens: int = 1024,
        temperature: float = 0.0,
        num_comps=1,
    ) -> Union[List[str], str]:
        global completion_tokens, prompt_tokens

        client = OpenAI(
            api_key=_bedrock_openai_api_key(),
            base_url=_bedrock_openai_base_url(),
        )

        system_prompt = ""
        user_chunks = []
        for msg in messages:
            content = msg.content.template if hasattr(msg.content, "template") else msg.content
            if msg.role == "system":
                system_prompt = f"{system_prompt}\n\n{content}".strip() if system_prompt else content
            else:
                user_chunks.append(f"{msg.role}: {content}" if msg.role == "assistant" else content)

        response = client.responses.create(
            model=model,
            instructions=system_prompt or None,
            input="\n\n".join(user_chunks),
            max_output_tokens=max_tokens,
            temperature=temperature,
        )

        usage = getattr(response, "usage", None)
        prompt_tokens += int(getattr(usage, "input_tokens", 0) or 0)
        completion_tokens += int(getattr(usage, "output_tokens", 0) or 0)
        return _responses_output_text(response)


class ModelBase:
    """Base class for all models"""

    def __init__(self, name: str):
        self.name = name
        self.last_response = None  # Store last response
        self.last_prompt = None  # Store last prompt

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name})"

    def generate_chat(
        self,
        messages: List[Message],
        max_tokens: int = 1024,
        temperature: float = 0.0,
        num_comps: int = 1,
    ) -> Union[List[str], str, Dict[str, str]]:
        # Store last prompt
        self.last_prompt = messages[-1].content if messages else None

        # Get response from implementation
        response = self._generate_chat_impl(
            messages, max_tokens, temperature, num_comps
        )

        # Store last response
        if isinstance(self, Claude_3_7_Sonnet_thinking):
            # For Claude thinking model, store both content and reasoning
            self.last_response = response
            # Return both content and reasoning
            return response
        else:
            # For non-Claude models, store and return the full response
            self.last_response = response if isinstance(response, str) else response[0]
            return response

    def _generate_chat_impl(
        self,
        messages: List[Message],
        max_tokens: int = 1024,
        temperature: float = 0.0,
        num_comps: int = 1,
    ) -> Union[List[str], str]:
        """Implementation of chat generation. To be overridden by subclasses."""
        raise NotImplementedError("Subclasses must implement _generate_chat_impl")


# class GPTChat(ModelBase):
#     """OpenAI GPT chat models"""

#     def __init__(self, model_name: str):
#         super().__init__(model_name)

#     def _generate_chat_impl(
#         self,
#         messages: List[Message],
#         max_tokens: int = 1024,
#         temperature: float = 0.0,
#         num_comps: int = 1,
#     ) -> Union[List[str], str]:
#         return gpt_chat(self.name, messages, max_tokens, temperature, num_comps)


# class AnthropicChat(ModelBase):
#     """Anthropic Claude models"""

#     def __init__(self, model_name: str):
#         super().__init__(model_name)

#     def _generate_chat_impl(
#         self,
#         messages: List[Message],
#         max_tokens: int = 1024,
#         temperature: float = 0.0,
#         num_comps: int = 1,
#     ) -> Union[List[str], str]:
#         return anthropic_chat(self.name, messages, max_tokens, temperature, num_comps)


class OpenRouterChat(ModelBase):
    """OpenRouter models"""

    def __init__(self, model_name: str):
        super().__init__(model_name)

    @retry(wait=wait_random_exponential(min=1, max=180), stop=stop_after_attempt(6))
    def _generate_chat_impl(
        self,
        messages: List[Message],
        max_tokens: int = 1024,
        temperature: float = 0.0,
        num_comps: int = 1,
    ) -> Union[List[str], str]:
        return openrouter_chat(self.name, messages, max_tokens, temperature, num_comps)


# OpenRouter model implementations
class ChatGPT4o(OpenRouterChat):
    def __init__(self):
        super().__init__("openai/chatgpt-4o-latest")


class ChatGPT4o_mini(OpenRouterChat):
    """GPT-4o mini model - faster and cheaper than full GPT-4o"""
    def __init__(self):
        super().__init__("openai/gpt-4o-mini")


class ChatGPT4_turbo(OpenRouterChat):
    """GPT-4 Turbo model - balance of power and speed"""
    def __init__(self):
        super().__init__("openai/gpt-4-turbo")


class ChatGPT_3_5_turbo(OpenRouterChat):
    """GPT-3.5 Turbo model - fast and economical"""
    def __init__(self):
        super().__init__("openai/gpt-3.5-turbo")


class Claude_3_7_Sonnet_thinking(OpenRouterChat):
    def __init__(self):
        super().__init__("anthropic/claude-3-7-sonnet-20250219:thinking")
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=openrouter_api_key,
        )
        # Initialize token usage tracking
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.request_count = 0

    @retry(wait=wait_random_exponential(min=1, max=180), stop=stop_after_attempt(6))
    def _generate_chat_impl(
        self,
        messages: List[Message],
        max_tokens: int = 2048,
        temperature: float = 0.0,
        num_comps: int = 1,
    ) -> Union[List[str], str, Dict[str, str]]:
        # Convert messages to OpenAI format
        openai_messages = [
            {"role": msg.role, "content": msg.content} for msg in messages
        ]

        # Call API directly using OpenAI client
        completion = self.client.chat.completions.create(
            model=self.name,
            messages=openai_messages,
            max_tokens=max_tokens,
            temperature=temperature,
            extra_headers={
                "HTTP-Referer": "https://github.com/anonymous",  # Optional
                "X-Title": "Anonymous",  # Optional
            },
        )

        # Store the full completion object for debugging
        self.last_completion = completion

        # Update token usage
        if hasattr(completion, "usage"):
            self.total_prompt_tokens += completion.usage.prompt_tokens
            self.total_completion_tokens += completion.usage.completion_tokens
            self.request_count += 1

        # Get both content and reasoning
        content = completion.choices[0].message.content
        reasoning = getattr(completion.choices[0].message, "reasoning", None)

        # Store both in last_response
        self.last_response = {"content": content, "reasoning": reasoning}

        # Return both content and reasoning
        return {"content": content, "reasoning": reasoning}

    def get_token_usage(self):
        """Get token usage statistics."""
        return {
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "request_count": self.request_count,
        }

    def get_last_completion(self):
        """Get the last completion object with full response details."""
        return getattr(self, "last_completion", None)

    def get_last_reasoning(self):
        """Get the last reasoning from the model's response."""
        if hasattr(self, "last_response") and isinstance(self.last_response, dict):
            return self.last_response.get("reasoning")
        return None


class Llama3_8B(OpenRouterChat):
    def __init__(self):
        super().__init__("meta-llama/llama-3-8b-instruct")


class Gemma_2_9B_IT_Free(OpenRouterChat):
    """Google Gemma 2.9B IT Free model through OpenRouter"""

    def __init__(self):
        super().__init__("google/gemma-2-9b-it:free")


class Gemma_2_9B_IT(OpenRouterChat):
    """Google Gemma 2.9B IT model through OpenRouter"""

    def __init__(self):
        super().__init__("google/gemma-2-9b-it")


class Mistral_24B_Instruct(OpenRouterChat):
    def __init__(self):
        super().__init__("mistralai/mistral-small-24b-instruct-2501")


class GPT5(OpenRouterChat):
    """OpenAI GPT-5 model through OpenRouter"""
    
    def __init__(self):
        super().__init__("openai/gpt-5")


class OllamaChat(ModelBase):
    """Ollama models running locally"""
    
    def __init__(self, model_name: str):
        super().__init__(model_name)
    
    @retry(wait=wait_random_exponential(min=1, max=180), stop=stop_after_attempt(6))
    def _generate_chat_impl(
        self,
        messages: List[Message],
        max_tokens: int = 1024,
        temperature: float = 0.0,
        num_comps: int = 1,
    ) -> Union[List[str], str]:
        return ollama_chat(self.name, messages, max_tokens, temperature, num_comps)


class Gemma2_9B_Ollama(OllamaChat):
    """Google Gemma 2 9B model through Ollama (local, free)"""
    
    def __init__(self):
        super().__init__("gemma2:9b")


@retry(wait=wait_random_exponential(min=1, max=180), stop=stop_after_attempt(6))
def bedrock_chat(
    model_id: str,
    messages: List[Message],
    max_tokens: int = 1024,
    temperature: float = 0.0,
    num_comps=1,
) -> Union[List[str], str]:
    global completion_tokens, prompt_tokens

    region_name = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    client = boto3.client("bedrock-runtime", region_name=region_name)

    system_prompt = ""
    user_chunks = []
    for msg in messages:
        content = msg.content.template if hasattr(msg.content, "template") else msg.content
        if msg.role == "system":
            system_prompt = f"{system_prompt}\n\n{content}".strip() if system_prompt else content
        else:
            user_chunks.append(f"{msg.role}: {content}" if msg.role == "assistant" else content)

    response = client.converse(
        modelId=model_id,
        system=[{"text": system_prompt}] if system_prompt else [],
        messages=[
            {
                "role": "user",
                "content": [{"text": "\n\n".join(user_chunks)}],
            }
        ],
        inferenceConfig={
            "temperature": temperature,
            "maxTokens": max_tokens,
        },
    )

    usage = response.get("usage", {})
    prompt_tokens += int(usage.get("inputTokens", 0))
    completion_tokens += int(usage.get("outputTokens", 0))

    content_blocks = response.get("output", {}).get("message", {}).get("content", [])
    return "".join(block.get("text", "") for block in content_blocks if "text" in block)


def _bedrock_region() -> str:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"


def _regionalize_bedrock_model_id(model_id: str) -> str:
    """Adapt a cross-region inference-profile prefix to the active AWS region.

    Bedrock inference profiles are prefixed by geography (`us.`, `eu.`, `apac.`)
    or by `global.`. A `us.`-prefixed id is rejected with "The provided model
    identifier is invalid" when running in eu-north-1, so a geographic prefix is
    swapped to match the configured region.

    `global.` profiles are already region-agnostic and are left untouched: some
    models (e.g. the GPT-5.6 family) are ONLY published under `global.`, and
    rewriting them to `eu.` would make them unresolvable.
    Override the target prefix with BEDROCK_MODEL_ID_PREFIX.
    """
    if model_id.startswith("global."):
        return model_id

    prefix_override = os.environ.get("BEDROCK_MODEL_ID_PREFIX")
    region = _bedrock_region()

    if prefix_override:
        target = prefix_override.strip().rstrip(".")
    elif region.startswith("eu-"):
        target = "eu"
    elif region.startswith("us-"):
        target = "us"
    elif region.startswith(("ap-", "apac-")):
        target = "apac"
    else:
        return model_id

    for known in ("us.", "eu.", "apac."):
        if model_id.startswith(known):
            return f"{target}.{model_id[len(known):]}"
    return model_id


class BedrockChat(ModelBase):
    """AWS Bedrock models"""

    def __init__(self, alias_name: str, model_id: Optional[str] = None):
        super().__init__(alias_name)
        raw_model_id = model_id or os.environ.get(
            "BEDROCK_MODEL_ID",
            "us.anthropic.claude-sonnet-4-6",
        )
        self.bedrock_model_id = _regionalize_bedrock_model_id(raw_model_id)

    @retry(wait=wait_random_exponential(min=1, max=180), stop=stop_after_attempt(6))
    def _generate_chat_impl(
        self,
        messages: List[Message],
        max_tokens: int = 1024,
        temperature: float = 0.0,
        num_comps: int = 1,
    ) -> Union[List[str], str]:
        return bedrock_chat(self.bedrock_model_id, messages, max_tokens, temperature, num_comps)


class BedrockClaude37Sonnet(BedrockChat):
    def __init__(self):
        super().__init__("bedrock-claude-sonnet-4-6", "us.anthropic.claude-sonnet-4-6")


class BedrockGPTOss120B(BedrockChat):
    def __init__(self):
        super().__init__("bedrock-gpt-oss-120b", "openai.gpt-oss-120b-1:0")


class BedrockOpenAIChat(ModelBase):
    """OpenAI models served through Amazon Bedrock Mantle"""

    def __init__(self, alias_name: str, model_id: str):
        super().__init__(alias_name)
        self.bedrock_model_id = model_id

    @retry(wait=wait_random_exponential(min=1, max=180), stop=stop_after_attempt(6))
    def _generate_chat_impl(
        self,
        messages: List[Message],
        max_tokens: int = 1024,
        temperature: float = 0.0,
        num_comps: int = 1,
    ) -> Union[List[str], str]:
        if not _bedrock_openai_api_key():
            raise ValueError(
                "BEDROCK_OPENAI_API_KEY (or BEDROCK_API_KEY / OPENAI_API_KEY) is required for Bedrock OpenAI runs."
            )
        return bedrock_openai_chat(self.bedrock_model_id, messages, max_tokens, temperature, num_comps)


class BedrockGPT55(BedrockOpenAIChat):
    def __init__(self):
        super().__init__("bedrock-gpt-5.5", "openai.gpt-5.5")

