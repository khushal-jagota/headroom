"""Codex app-server login protocol bindings. Generated — do not edit.

Regenerate with:
    python -m planner.conversation.backends.codex_app_server.generate_bindings --bundle login

The pin these models were generated under:

    bundle                 login
    codex binary version   codex-cli 0.163.0
    upstream openai/codex  rust-v0.163.0 = 83086188ae0614bd882818ff1aa23f3194962012
    schema obtained by     codex app-server generate-json-schema --out <dir>
    from the dump's        codex_app_server_protocol.schemas.json
    dump digest (sha256)   5bc6033305d9eb75a5ddd8488968445256c0ca4481ac2daf55d4ea46712b703f
    pruned and vendored    schema/codex_app_server_protocol.login.subset.schema.json
    subset digest (sha256) a16252f6718e58c235efec970c4e7c259d93462e8519adc7ad91c0b78646d719
    definitions generated  8

The digests are taken over the JSON's meaning — keys sorted — so they change
when the protocol changes and not when the dump is printed differently.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, RootModel


class Model(RootModel[Any]):
    root: Any


class CancelLoginAccountParams(BaseModel):
    loginId: str


class ApiKeyv2LoginAccountParams(BaseModel):
    apiKey: str
    type: Annotated[Literal["apiKey"], Field(title="ApiKeyv2::LoginAccountParamsType")]


class ChatgptDeviceCodev2LoginAccountParams(BaseModel):
    type: Annotated[
        Literal["chatgptDeviceCode"], Field(title="ChatgptDeviceCodev2::LoginAccountParamsType")
    ]


class ChatgptAuthTokensv2LoginAccountParams(BaseModel):
    accessToken: str
    chatgptAccountId: str
    chatgptPlanType: str | None = None
    type: Annotated[
        Literal["chatgptAuthTokens"], Field(title="ChatgptAuthTokensv2::LoginAccountParamsType")
    ]


class AmazonBedrockv2LoginAccountParams(BaseModel):
    apiKey: str
    region: str
    type: Annotated[
        Literal["amazonBedrock"], Field(title="AmazonBedrockv2::LoginAccountParamsType")
    ]


class AmazonBedrockAccessKeysv2LoginAccountParams(BaseModel):
    accessKeyId: str
    region: str
    secretAccessKey: str
    sessionToken: str | None = None
    type: Annotated[
        Literal["amazonBedrockAccessKeys"],
        Field(title="AmazonBedrockAccessKeysv2::LoginAccountParamsType"),
    ]


class ApiKeyv2LoginAccountResponse(BaseModel):
    type: Annotated[Literal["apiKey"], Field(title="ApiKeyv2::LoginAccountResponseType")]


class Chatgptv2LoginAccountResponse(BaseModel):
    authUrl: str
    loginId: str
    type: Annotated[Literal["chatgpt"], Field(title="Chatgptv2::LoginAccountResponseType")]


class ChatgptDeviceCodev2LoginAccountResponse(BaseModel):
    loginId: str
    type: Annotated[
        Literal["chatgptDeviceCode"], Field(title="ChatgptDeviceCodev2::LoginAccountResponseType")
    ]
    userCode: str
    verificationUrl: str


class ChatgptAuthTokensv2LoginAccountResponse(BaseModel):
    type: Annotated[
        Literal["chatgptAuthTokens"], Field(title="ChatgptAuthTokensv2::LoginAccountResponseType")
    ]


class AmazonBedrockv2LoginAccountResponse(BaseModel):
    type: Annotated[
        Literal["amazonBedrock"], Field(title="AmazonBedrockv2::LoginAccountResponseType")
    ]


class LoginAccountResponse(
    RootModel[
        ApiKeyv2LoginAccountResponse
        | Chatgptv2LoginAccountResponse
        | ChatgptDeviceCodev2LoginAccountResponse
        | ChatgptAuthTokensv2LoginAccountResponse
        | AmazonBedrockv2LoginAccountResponse
    ]
):
    root: Annotated[
        ApiKeyv2LoginAccountResponse
        | Chatgptv2LoginAccountResponse
        | ChatgptDeviceCodev2LoginAccountResponse
        | ChatgptAuthTokensv2LoginAccountResponse
        | AmazonBedrockv2LoginAccountResponse,
        Field(title="LoginAccountResponse"),
    ]


class AccountLoginCompletedNotification(BaseModel):
    error: str | None = None
    loginId: str | None = None
    onboardingEntrypoint: Literal["life_sciences"] | None = None
    success: bool


class CancelLoginAccountResponse(BaseModel):
    status: Literal["canceled", "notFound"]


class Chatgptv2LoginAccountParams(BaseModel):
    appBrand: Literal["codex", "chatgpt"] | None = None
    codexStreamlinedLogin: bool | None = None
    type: Annotated[Literal["chatgpt"], Field(title="Chatgptv2::LoginAccountParamsType")]
    useHostedLoginSuccessPage: bool | None = None


class LoginAccountParams(
    RootModel[
        ApiKeyv2LoginAccountParams
        | Chatgptv2LoginAccountParams
        | ChatgptDeviceCodev2LoginAccountParams
        | ChatgptAuthTokensv2LoginAccountParams
        | AmazonBedrockv2LoginAccountParams
        | AmazonBedrockAccessKeysv2LoginAccountParams
    ]
):
    root: Annotated[
        ApiKeyv2LoginAccountParams
        | Chatgptv2LoginAccountParams
        | ChatgptDeviceCodev2LoginAccountParams
        | ChatgptAuthTokensv2LoginAccountParams
        | AmazonBedrockv2LoginAccountParams
        | AmazonBedrockAccessKeysv2LoginAccountParams,
        Field(title="LoginAccountParams"),
    ]
