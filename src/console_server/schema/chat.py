from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=16000)

    @field_validator("content")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("消息不能为空")
        return value


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(min_length=1, max_length=100)

    @field_validator("messages")
    @classmethod
    def validate_history(cls, messages: list[ChatMessage]) -> list[ChatMessage]:
        if messages[0].role != "user" or messages[-1].role != "user":
            raise ValueError("对话必须由用户开始，最后一条必须是用户消息")
        if any(a.role == b.role for a, b in zip(messages, messages[1:])):
            raise ValueError("用户和助手消息必须交替出现")
        if sum(len(message.content) for message in messages) > 64000:
            raise ValueError("对话过长，请新建对话")
        return messages
