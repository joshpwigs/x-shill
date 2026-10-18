from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
import re


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class Fact(Strict):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,48}$")
    text: str = Field(min_length=1, max_length=1000)
    classification: Literal["verified", "opinion", "unverified"] = "unverified"
    source: str = Field(default="", max_length=1000)
    verified_at: float | None = None
    stale_after_days: int = Field(default=30, ge=1, le=365)

    @field_validator("source")
    @classmethod
    def url(cls, value):
        if value and (urlsplit(value).scheme != "https" or not urlsplit(value).hostname or urlsplit(value).username):
            raise ValueError("Sources must be HTTPS URLs without credentials")
        return value


class Profile(Strict):
    name: str = Field(min_length=1, max_length=100)
    ticker: str = Field(default="", max_length=20)
    chain: str = Field(default="", max_length=100)
    contract_address: str = Field(default="", max_length=200)
    contract_fact_id: str = Field(default="", max_length=48)
    account: str = Field(pattern=r"^[A-Za-z0-9_]{1,15}$")
    official_links: list[str] = Field(default_factory=list, max_length=20)
    facts: list[Fact] = Field(default_factory=list, max_length=200)
    audience: str = Field(default="", max_length=2000)
    slang: list[str] = Field(default_factory=list, max_length=30)
    jokes: list[str] = Field(default_factory=list, max_length=30)
    competitors: list[str] = Field(default_factory=list, max_length=30)
    voice: str = Field(default="Dry humor; clear and useful.", max_length=2000)
    good_examples: list[str] = Field(default_factory=list, max_length=20)
    bad_examples: list[str] = Field(default_factory=list, max_length=20)
    disclosure: str = Field(min_length=1, max_length=100)
    forbidden_claims: list[str] = Field(default_factory=list, max_length=50)
    keywords: list[str] = Field(default_factory=list, max_length=30)
    monitored_accounts: list[str] = Field(default_factory=list, max_length=30)
    fictional: bool = True

    @field_validator("official_links")
    @classmethod
    def links(cls, values):
        return [Fact.url(v) for v in values]

    @field_validator("facts")
    @classmethod
    def unique_facts(cls, values):
        if len({f.id for f in values}) != len(values):
            raise ValueError("Fact IDs must be unique")
        return values


class Opportunity(Strict):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    author: str = Field(pattern=r"^[A-Za-z0-9_]{1,15}$")
    text: str = Field(min_length=1, max_length=10000)
    url: str = Field(max_length=300)
    posted_at: float
    thread_id: str = Field(max_length=80)
    context: str = Field(default="", max_length=5000)
    inbound: bool = False
    replies: int = Field(default=0, ge=0)
    likes: int = Field(default=0, ge=0)
    fictional: bool = False

    @field_validator("url")
    @classmethod
    def post_link(cls, value):
        u = urlsplit(value)
        if u.scheme != "https" or u.hostname not in {"x.com", "twitter.com"} or u.username:
            raise ValueError("Use an original HTTPS X post URL")
        if not re.fullmatch(r"/[A-Za-z0-9_]{1,15}/status/[0-9]+", u.path):
            raise ValueError("Expected /account/status/post_id")
        return value

    @model_validator(mode="after")
    def consistent_author(self):
        if urlsplit(self.url).path.split('/')[1].casefold()!=self.author.casefold():
            raise ValueError("The post URL must match the attributed author")
        return self


class DraftBody(Strict):
    text: str = Field(min_length=1, max_length=5000)
    kind: Literal["post", "meme", "update", "education", "opinion", "question", "reply", "quote"] = "post"
    tone: Literal["dry", "sarcastic", "absurd", "community", "straightforward"] = "dry"
    topic: str = Field(default="community", max_length=100)
    hook: str = Field(default="question", max_length=100)
    cta: str = Field(default="none", max_length=100)
    source_ids: list[str] = Field(default_factory=list, max_length=30)
    opportunity_id: str | None = Field(default=None, max_length=80)
    image_brief: str = Field(default="", max_length=3000)
    fictional: bool = True


class Generate(Strict):
    kind: DraftBody.model_fields["kind"].annotation = "post"
    tone: DraftBody.model_fields["tone"].annotation = "dry"
    topic: str = Field(default="community", min_length=1, max_length=100)
    opportunity_id: str | None = Field(default=None, max_length=80)


class EditDraft(Strict):
    revision: int
    body: DraftBody


class Approve(Strict):
    revision: int
    fingerprint: str
    facts_reviewed: bool
    context_reviewed: bool


class Schedule(Strict):
    revision: int
    due_at: float


class Metrics(Strict):
    captured_at: float
    impressions: int | None = Field(default=None, ge=0)
    likes: int | None = Field(default=None, ge=0)
    replies: int | None = Field(default=None, ge=0)
    meaningful_replies: int | None = Field(default=None, ge=0)
    reposts: int | None = Field(default=None, ge=0)
    profile_visits: int | None = Field(default=None, ge=0)
    clicks: int | None = Field(default=None, ge=0)
    qualified_clicks: int | None = Field(default=None, ge=0)
    source: str = Field(min_length=1, max_length=200)


class Reconcile(Strict):
    # An uncertain send can ONLY be closed with a confirmed receipt; never automatically retried.
    post_url: str

    @field_validator("post_url")
    @classmethod
    def link(cls, value):
        return Opportunity.post_link(value)
