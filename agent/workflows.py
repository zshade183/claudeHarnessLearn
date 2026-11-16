import asyncio
import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from uuid import uuid4

AGENT_CAP = 1000  # 一次执行最多进入 ctx.agent() 的次数
CONCURRENCY = 8   # 一次执行同时等待模型响应的请求数上限

JsonValue = dict | list | str | int | float | bool | None
_MISSING = object()  # JSON 的 null 也是合法答案，不能用 None 表示未命中。
_JSON_TYPES = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "boolean": (bool,),
    "integer": (int,),
    "number": (int, float),
    "null": (type(None),),
}

# 工作流名称正则表达式
WORKFLOW_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")

# 工作流解释 JSON 模式
EXPLANATION_SCHEMA = {
    "type": "object",
    "properties": {"explanation": {"type": "string"}},
    "required": ["explanation"],
    "additionalProperties": False,
}


class WorkflowInputError(ValueError):
    """工作流名称、登记信息或调用参数有误。"""


def validate_meta(meta: dict) -> None:
    """在启动前检查工作流的登记信息。
    登记信息，比如这个流程叫什么、做什么、有哪些阶段、每个阶段需要做什么等
    等。
    """
    if not isinstance(meta, dict):
        raise WorkflowInputError("Workflow metadata must be a dictionary")

    name = meta.get("name")
    # 检查工作流名称是否符合要求
    if not isinstance(name, str) or not WORKFLOW_NAME_RE.fullmatch(name):
        raise WorkflowInputError("Invalid workflow name")
    # 检查工作流描述是否符合要求
    description = meta.get("description")
    if not isinstance(description, str) or not description.strip():
        raise WorkflowInputError("Workflow description must be non-empty text")
    # 检查工作流阶段是否符合要求
    phases = meta.get("phases", [])
    if not isinstance(phases, list) or any(
        not isinstance(phase, str) or not phase.strip()
        for phase in phases
    ):
        raise WorkflowInputError("Workflow phases must be a list of non-empty strings")


def validate_json(value: JsonValue, schema: dict, path: str = "$") -> None:
    """沿字段和数组下标递归检查，并用路径指出出错的位置。"""
    kind = schema.get("type")
    if kind is not None:
        if kind not in _JSON_TYPES:
            raise WorkflowInputError(f"{path}: unsupported schema type {kind!r}")
        # 用实际类型比较，避免 Python 把 True 当作整数 1 接受。
        if type(value) not in _JSON_TYPES[kind]:
            raise WorkflowInputError(f"{path}: expected {kind}")
        if kind == "number" and type(value) is float and not isfinite(value):
            raise WorkflowInputError(f"{path}: expected a finite number")

    if "enum" in schema and value not in schema["enum"]:
        raise WorkflowInputError(f"{path}: expected one of {schema['enum']!r}")

    if kind == "object":
        properties = schema.get("properties", {})
        for field in schema.get("required", []):
            if field not in value:
                raise WorkflowInputError(f"{path}.{field}: required field is missing")
        for field, entry in value.items():
            if field in properties:
                validate_json(entry, properties[field], f"{path}.{field}")
            elif schema.get("additionalProperties") is False:
                raise WorkflowInputError(f"{path}.{field}: unexpected field")
    elif kind == "array" and "items" in schema:
        for index, entry in enumerate(value):
            validate_json(entry, schema["items"], f"{path}[{index}]")


def parse_agent_json(raw: str, schema: dict) -> JsonValue:
    """先解析模型的 JSON 文本，再用 schema 检查解析后的值。"""
    raw = raw.strip()
    first, _, body = raw.partition("\n")
    if first.lower().strip() in {"```", "```json"} and body.endswith("\n```"):
        raw = body.removesuffix("\n```")
    value = json.loads(raw)
    validate_json(value, schema)
    return value


def read_resume_args(path: Path, name: str, args: dict | None) -> dict:
    """核对旧快照，取出本次续跑要用的原始参数。"""
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise WorkflowInputError("Resume snapshot not found") from error
    except (json.JSONDecodeError, UnicodeError) as error:
        raise WorkflowInputError("Invalid resume snapshot") from error

    if not isinstance(snapshot, dict) or snapshot.get("runId") != path.stem:
        raise WorkflowInputError("Invalid resume snapshot")
    if snapshot.get("workflowName") != name:
        raise WorkflowInputError("Resume runId belongs to a different workflow")

    saved_args = snapshot.get("args")
    if not isinstance(saved_args, dict):
        raise WorkflowInputError("Invalid resume snapshot arguments")
    if args is not None and args != saved_args:
        raise WorkflowInputError("Resume args do not match the original run")
    return saved_args


def write_workflow_json(path: Path, value: object) -> None:
    """把 Python 数据保存到指定的 JSON 文件。"""
    # 第一次运行时，先创建 .runtime 目录。
    path.parent.mkdir(parents=True, exist_ok=True)

    # 先把完整内容写到同一个目录里的临时文件。
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 临时文件写完后，再用它替换正式文件。
    temporary.replace(path)


class WorkflowJournal:
    """
    保存一次工作流运行中已经完成的模型步骤。
    """

    def __init__(self, path: Path, *, resume: bool = False) -> None:
        self.path = path  # 记录工作流运行时的文件路径
        self._cache: dict[str, JsonValue] = {}  # 缓存已经读取的答案
        self._needs_newline = False

        if not resume:
            # 新运行先建一个空文件。若同名文件已存在，就报错以保护旧记录。
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=False)
            return

        # 续跑时，一行一行读旧 journal，变成能按 key 查询的字典。
        try:
            with path.open(encoding="utf-8") as file:
                for number, line in enumerate(file, start=1):
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError as error:
                        raise WorkflowInputError(
                            f"Invalid journal JSON at line {number}"
                        ) from error

                    if (
                        not isinstance(entry, dict)
                        or not isinstance(entry.get("key"), str)
                        or "value" not in entry
                    ):
                        raise WorkflowInputError(
                            f"Invalid journal entry at line {number}"
                        )
                    self._cache[entry["key"]] = entry["value"]
                    # 最后一行可能已有完整 JSON，只差一个换行符。
                    self._needs_newline = not line.endswith("\n")
        except FileNotFoundError as error:
            raise WorkflowInputError("Resume journal not found") from error
        except UnicodeError as error:
            raise WorkflowInputError("Invalid journal encoding") from error

    def cached(self, key: str) -> JsonValue | object:
        """按步骤编号取答案，用哨兵区分未命中和 JSON null。"""
        return self._cache.get(key, _MISSING)

    @staticmethod
    def agent_key(
        label: str, prompt: str, schema: dict | None, *, workflow_name: str | None = None
    ) -> str:
        """用步骤标签、原始提问和答案格式一起确定这次调用的编号。
        params:
            label: 一个字符串，用于标识这个步骤。
            prompt: 一个字符串，包含模型的输入提示。
            schema: 一个 JSON 模式，定义了模型返回的 JSON 对象的结构。
        return:
            一个字符串，包含这次调用的编号。
        """
        # 把步骤标签、原始提问和答案格式合并成一个列表，确保编号的唯一性。
        # 同时，用 JSON 字符串表示，确保编号的可预测性。
        parts = ["agent", label, prompt, schema]
        if workflow_name is not None:
            # 子流程有自己的命名范围。顶层步骤仍能命中原来保存的 key。
            parts.append(workflow_name)
        basis = json.dumps(parts, ensure_ascii=False, sort_keys=True)
        # 用 SHA-256 算出固定长度的哈希。同样的输入会得到同样的编号。
        digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()
        return f"agent-{digest}"

    def record(self, key: str, value: JsonValue) -> None:
        """一行里同时保存步骤编号和这一步得到的答案。
        key找的是文件里的某个答案。 比如同一份 journal 里，解释 MCP 和解释 Hooks 各有自己的 key
        params:
            key: 一个字符串，包含这次调用的编号。
            value: 这一步得到的纯文本或结构化 JSON 值。
        """
        line = json.dumps({"key": key, "value": value}, ensure_ascii=False)
        # "a" 是追加模式。离开 with 时，Python 会把缓冲内容写出并关闭文件。
        with self.path.open("a", encoding="utf-8", newline="\n") as file:
            prefix = "\n" if self._needs_newline else ""
            file.write(prefix + line + "\n")
        self._needs_newline = False
        self._cache[key] = value


@dataclass(frozen=True)
class RunnerOutput:
    """一次模型请求返回的文字和 token 用量。"""

    text: str
    tokens: int


class LocalWorkflowTask:
    """记录一次工作流运行的编号和状态。"""

    def __init__(self, workflow_name: str, *, run_id: str | None = None) -> None:
        self.run_id = uuid4().hex if run_id is None else run_id
        self.task_id = f"local_workflow_{self.run_id}"
        self.workflow_name = workflow_name  # 工作流名称
        self.status = "running"  # 运行状态
        self.error: str | None = None  # 错误信息
        self.usage = {"agents": 0, "tokens": 0}
        self.progress: list[dict] = []

    def event(self, name: str, **fields) -> None:
        """立即打印生命周期事件，终端可以边执行边看到进度。"""
        details = " ".join(f"{key}={value}" for key, value in fields.items())
        print(f"  [{name}] runId={self.run_id} taskId={self.task_id} {details}", flush=True)

    def progress_event(self, kind: str, **fields) -> None:
        # 同一条进度既写到任务记录，也立刻显示给用户。
        self.progress.append({"type": kind, **fields})
        self.event("task_progress", progressType=kind, **fields)

    def launch_info(self) -> dict:
        return {
            "status": "async_launched",
            "runId": self.run_id,
            "taskId": self.task_id,
            "taskType": "local_workflow",
            "workflowName": self.workflow_name,
        }

    def to_dict(self) -> dict:
        """把本次运行的记录转成可以返回给调用方的字典。"""
        return {
            "taskId": self.task_id,
            "taskType": "local_workflow",
            "runId": self.run_id,
            "workflowName": self.workflow_name,
            "status": self.status,
            "error": self.error,
            "usage": dict(self.usage),
            "progress": list(self.progress),
        }


class Budget:
    """限制一次工作流执行可使用的模型 token。"""

    def __init__(self, total: int | None) -> None:
        # None 表示省略预算。只接受非负整数，布尔值也会被拒绝。
        if total is not None and (type(total) is not int or total < 0):
            raise WorkflowInputError("Workflow budget must be a non-negative integer")
        self.total = total

    def check_before_request(self, spent: int) -> None:
        # 已用 100 / 预算 100 时，不能再发新请求。
        if self.total is not None and spent >= self.total:
            raise WorkflowInputError(f"Token budget exhausted ({spent}/{self.total})")

    def check_after_request(self, spent: int) -> None:
        # 最后一次响应回来刚好用满预算，仍然可以接受。
        if self.total is not None and spent > self.total:
            raise WorkflowInputError(f"Token budget exceeded ({spent} > {self.total})")


class ExecutionLimits:
    """管理一次工作流执行中的步骤数和并发请求数。"""

    def __init__(self) -> None:
        self.agents = 0
        self.semaphore = asyncio.Semaphore(CONCURRENCY)

    def claim_agent(self) -> None:
        """为一个 ctx.agent() 步骤计数，达到上限时拒绝新步骤。"""
        if self.agents >= AGENT_CAP:
            raise WorkflowInputError(f"agent() cap reached ({AGENT_CAP})")
        self.agents += 1


class ExecutionState:
    """提供工作流可以调用的步骤，并记录本次运行的进度。"""

    def __init__(
        self,
        run_agent: Callable[[str], RunnerOutput],
        *,
        journal: WorkflowJournal,
        task: LocalWorkflowTask,
        budget: Budget,
        limits: ExecutionLimits | None = None,
        workflow_name: str | None = None,
    ) -> None:
        self._run_agent = run_agent
        self.journal = journal
        self.task = task
        self.budget = budget
        self._limits = ExecutionLimits() if limits is None else limits
        self._workflow_name = workflow_name  # 仅子流程设置，用于区分它的事件和缓存。
        self._phases_seen: set[str] = set()
        self.current_phase: str | None = None  # 当前阶段
        self.progress = task.progress  # 父子流程共用一份进度记录。

    def _progress(self, kind: str, **fields) -> None:
        if self._workflow_name is not None:
            fields["workflowName"] = self._workflow_name
        self.task.progress_event(kind, **fields)

    def phase(self, title: str) -> None:
        """切换当前阶段，并记下一条阶段事件。"""
        self.current_phase = title
        if title not in self._phases_seen:
            self._phases_seen.add(title)
            self._progress("workflow_phase", title=title)

    def log(self, message: str) -> None:
        """记下一条进度说明，并标明它属于哪个阶段。"""
        self._progress("workflow_log", phase=self.current_phase, message=message)

    async def agent(
        self, prompt: str, *, label: str, schema: dict | None = None, phase: str | None = None
    ) -> JsonValue:
        """运行一个模型步骤。指定 schema 时，检查返回的 JSON 是否符合要求。
        params:
            prompt: 一个字符串，包含模型的输入提示。
            label: 一个字符串，用于标识这个步骤。
            schema: 一个 JSON 模式，定义了模型返回的 JSON 对象的结构。
            phase: 并行分支可自行指定阶段，默认使用当前阶段。
        return:
            模型返回的文字或通过校验的 JSON 值。
        """
        # 每进入一次 ctx.agent() 就计一次，包括命中 journal 的步骤。
        self._limits.claim_agent()
        phase = self.current_phase if phase is None else phase
        key = self.journal.agent_key(label, prompt, schema, workflow_name=self._workflow_name)
        cached = self.journal.cached(key)
        if cached is not _MISSING:
            if schema is not None:
                # 从磁盘读回的结构化答案也走一次原来的校验。
                validate_json(cached, schema)
            elif not isinstance(cached, str) or not cached.strip():
                raise WorkflowInputError(f"{label}: cached output must be non-empty text")
            self._progress("workflow_agent", phase=phase, label=label, status="cached")
            return cached

        request = prompt
        if schema is not None:
            request += "\n只返回 JSON 对象，遵循此格式：" + json.dumps(schema)

        for attempt in range(2):
            async with self._limits.semaphore:
                # 轮到自己发请求时，看其他步骤是否已经用完预算。
                self.budget.check_before_request(self.task.usage["tokens"])
                if attempt == 0:
                    # 同一个步骤即使重试，也只计一次 agents。
                    self.task.usage["agents"] += 1
                    self._progress("workflow_agent", phase=phase, label=label, status="started")

                response = await asyncio.to_thread(self._run_agent, request)

                # 先记下实际用量。即使超额，快照也应显示真实数字。
                self.task.usage["tokens"] += response.tokens
                self.budget.check_after_request(self.task.usage["tokens"])

            try:
                raw = response.text
                if not raw:
                    raise WorkflowInputError("Workflow agent returned no text")
                result = raw if schema is None else parse_agent_json(raw, schema)
                break
            except (json.JSONDecodeError, WorkflowInputError) as error:
                if schema is None:
                    raise
                if attempt == 1:
                    raise WorkflowInputError(
                        f"{label}: invalid structured output: {error}"
                    ) from error
                # 把具体的校验错误交给模型，再给它一次修正机会。
                request += f"\n上次返回不符合格式：{error}。请重新返回 JSON。"

        # 走到这里时，纯文本已经取出，结构化答案也已经通过校验。
        self.journal.record(key, result)
        self._progress("workflow_agent", phase=phase, label=label, status="done")
        return result

    async def parallel(self, steps):
        """同时启动多个步骤，等全部结束后按传入顺序返回结果。
        params:
            steps: 一个可调用对象列表，每个对象返回一个异步任务。
            返回一个列表，按传入顺序包含每个任务的结果。
        """
        async def run_step(start):
            return await start()

        # 并行启动所有步骤，等全部结束后返回结果。
        results = await asyncio.gather(
            *(run_step(start) for start in steps),
            return_exceptions=True,
        )

        # 一步失败时，等其他已启动的步骤完成并记录进度，再抛出错误。
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return results

    async def pipeline(self, items, *stages):
        """
        每项输入按顺序经过各阶段，不同输入同时推进。
        params:
            items: 一个可迭代对象，每个元素作为输入。
            stages: 一个可调用对象列表，每个对象返回一个异步任务。
            返回一个列表，按传入顺序包含每个任务的结果。
        return:
            一个列表，按传入顺序包含每个任务的结果。
        """
        async def run_item(item, index):
            """对单项输入按顺序通过各阶段，返回结果。"""
            value = item
            for stage in stages:
                # 这一阶段的返回值会成为下一阶段的第一个参数。
                value = await stage(value, item, index)
            return value

        # 每个函数记住当前的输入和位置，供 parallel 稍后调用。
        return await self.parallel([
            lambda item=item, index=index: run_item(item, index)
            for index, item in enumerate(items)
        ])

    async def workflow(self, name: str, args: dict | None = None) -> dict:
        """在同一次运行中执行一个已登记的子流程。"""
        if self._workflow_name is not None:
            raise WorkflowInputError("Workflow nesting is limited to one level")
        if args is not None and not isinstance(args, dict):
            raise WorkflowInputError("Workflow args must be an object")
        _, script = get_workflow(name)
        # 子流程有自己的阶段，模型调用与父流程使用相同的资源。
        child = ExecutionState(
            self._run_agent,
            journal=self.journal,
            task=self.task,
            budget=self.budget,
            limits=self._limits,
            workflow_name=name,
        )
        return await script(child, {} if args is None else args)


async def hello_workflow(ctx: ExecutionState, args: dict) -> dict:
    """读取主题，并行获取解释和例子，再组装结果。"""
    # 准备阶段
    ctx.phase("Prepare")
    topic = args.get("topic", "工作流")
    ctx.log(f"Topic: {topic}")
    # 解释阶段
    ctx.phase("Explain")
    explanation, example = await ctx.parallel([
        lambda: ctx.agent(
            f"请用一句话向初学者解释这个主题：{topic}",
            label="explain-topic",
        ),
        lambda: ctx.agent(
            f"请给初学者举一个与这个主题相关的例子：{topic}",
            label="give-example",
        ),
    ])
    return {
        "message": f"你好，{topic}！",
        "explanation": explanation,
        "example": example,
    }


async def topic_guide_workflow(ctx: ExecutionState, args: dict) -> dict:
    """
    为每个主题先写解释，再根据这份解释举例。
    params:
        topics: 一个可迭代对象，每个元素作为主题。
    return:
        一个列表，按传入顺序包含每个主题的解释和例子。
    """
    topics = args.get("topics", ["MCP", "Hooks"])
    if not isinstance(topics, list) or not topics or any(
        not isinstance(topic, str) or not topic.strip() for topic in topics
    ):
        raise WorkflowInputError("topics must be a non-empty list of non-empty strings")

    ctx.phase("Build")

    async def explain(_value, topic, index):
        data = await ctx.agent(
            f"请用一句话解释主题：{topic}",
            label=f"topic-{index + 1}:explain",
            schema=EXPLANATION_SCHEMA,
        )
        return data["explanation"]

    async def give_example(explanation, topic, index):
        example = await ctx.agent(
            f"主题：{topic}\n已有解释：{explanation}\n请根据这份解释举一个例子。",
            label=f"topic-{index + 1}:example",
        )
        return {"topic": topic, "explanation": explanation, "example": example}

    return {"guides": await ctx.pipeline(topics, explain, give_example)}


REVIEW_DIMENSIONS = ("correctness", "security", "performance", "style")
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
FINDINGS_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "severity": {"type": "string", "enum": list(SEVERITY_ORDER)},
                },
                "required": ["title", "severity"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["findings"],
    "additionalProperties": False,
}
VERDICT_SCHEMA = {
    "type": "object",
    "properties": {"isReal": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["isReal", "reason"],
    "additionalProperties": False,
}


async def review_changes_workflow(ctx: ExecutionState, args: dict) -> dict:
    """各审查维度独立走「审查 → 逐条复核」，最后只返回确认的问题。"""
    changes = args.get("changes", "")
    if not isinstance(changes, str):
        raise WorkflowInputError("args.changes must be a string")
    review_input = changes.strip() or "No change context was supplied."
    ctx.phase("Review")

    async def audit(_value, dimension, _index):
        return await ctx.agent(
            f"Review dimension: {dimension}\n"
            "Report only findings supported by the supplied code changes.\n\n"
            f"Change context:\n{review_input}",
            label=f"audit:{dimension}",
            schema=FINDINGS_SCHEMA,
            phase="Review",
        )

    async def verify(audited, dimension, _index):
        ctx.phase("Verify")
        findings = audited["findings"]

        async def verify_finding(finding):
            return await ctx.agent(
                f"Verify dimension: {dimension}\n"
                "Independently check whether this finding is supported by the changes.\n\n"
                f"Change context:\n{review_input}\n\n"
                f"Finding:\n{json.dumps(finding, ensure_ascii=False)}",
                label=f"verify:{dimension}:{finding['title']}",
                schema=VERDICT_SCHEMA,
                phase="Verify",
            )

        # 同一维度可以有多条候选问题，逐条并行验证后按原位置对应。
        verdicts = await ctx.parallel([
            lambda finding=finding: verify_finding(finding) for finding in findings
        ])
        return [
            {"dimension": dimension, **finding}
            for finding, verdict in zip(findings, verdicts)
            if verdict["isReal"]
        ]

    results = await ctx.pipeline(REVIEW_DIMENSIONS, audit, verify)
    confirmed = [finding for dimension in results for finding in dimension]
    confirmed.sort(key=lambda finding: SEVERITY_ORDER[finding["severity"]])
    ctx.log(f"Confirmed {len(confirmed)} findings")
    return {"confirmed": confirmed}


HELLO_META = {
    "name": "hello",
    "description": "Explain the supplied topic and give a short example.",
    "phases": ["Prepare", "Explain"],
}

TOPIC_GUIDE_META = {
    "name": "topic-guide",
    "description": "Explain each topic and give an example based on its explanation.",
    "phases": ["Build"],
}

REVIEW_CHANGES_META = {
    "name": "review-changes",
    "description": "Review supplied changes across dimensions and verify each finding.",
    "phases": ["Review", "Verify"],
}

WORKFLOWS = {
    HELLO_META["name"]: (HELLO_META, hello_workflow),
    TOPIC_GUIDE_META["name"]: (TOPIC_GUIDE_META, topic_guide_workflow),
    REVIEW_CHANGES_META["name"]: (REVIEW_CHANGES_META, review_changes_workflow),
}
for meta, _ in WORKFLOWS.values():
    validate_meta(meta)


def get_workflow(name: str) -> tuple[dict, Callable]:
    """顶层和子流程都从宿主登记表查找脚本，并按名称检查权限。"""
    if not isinstance(name, str) or name not in WORKFLOWS:
        raise WorkflowInputError(f"Unknown workflow: {name!r}")
    meta, script = WORKFLOWS[name]
    validate_meta(meta)
    denied = {entry.strip() for entry in os.getenv("WORKFLOW_DENY", "").split(",")}
    if name in denied:
        raise WorkflowInputError(f"Workflow {name!r} denied by WORKFLOW_DENY")
    return meta, script
