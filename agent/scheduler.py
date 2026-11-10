import json
import os
from datetime import datetime
import secrets
import threading
from dataclasses import asdict, dataclass
from agent.config import WORKDIR

@dataclass
class CronJob:
    """一条定时计划，以及它最近一次触发的状态。"""

    id: str        # 程序生成的唯一标识，查询和取消时用它。
    cron: str      # 什么时候触发，例如 "0 9 * * *"。
    prompt: str    # 到点时交给代理的指令，例如 "检查测试结果"。
    recurring: bool  # True 表示重复触发，False 表示只触发一次。
    durable: bool    # 是否需要写到磁盘，供程序重启后恢复。

    # 新任务还没到点。到点排队后会变成 True，表示等待交付给代理。
    pending_delivery: bool = False

    # 记录上次触发的分钟，防止调度器在同一分钟反复触发。
    last_fired: str | None = None


# 字典用任务 ID 做键。以后取消某个任务时，可以直接按 ID 找到它。
scheduled_jobs: dict[str, CronJob] = {}

# 登记任务和扫描到点任务会共享这张表，之后会用同一把锁协调访问。
cron_lock = threading.RLock()

DURABLE_PATH = WORKDIR / ".scheduled_tasks.json"

# 到点但还没交给代理的任务会进入这个列表。
# 重启时，从磁盘读到这类任务，也要把它放回队列。
cron_queue: list[CronJob] = []

def _cron_field_matches(field: str, value: int) -> bool:
    """用一个 cron 字段判断一个时间数字。"""

    # 星号表示这个字段不限，当前数字是多少都接受。
    if field == "*":
        return True

    # */15 表示当前数字是 15 的倍数。
    # 用在分钟字段时，会匹配 0、15、30、45。
    if field.startswith("*/"):
        step = int(field[2:])
        return value % step == 0

    # 9,17 拆成 "9" 和 "17"，交给同一个函数分别判断。
    # any() 只要找到一个匹配项，就会返回 True。
    if "," in field:
        return any(
            _cron_field_matches(part.strip(), value)
            for part in field.split(",")
        )

    # 1-5 包含两个端点，用在星期字段时表示周一到周五。
    if "-" in field:
        start, end = field.split("-", 1)
        return int(start) <= value <= int(end)

    # 普通数字要求精确相等，例如小时字段 9 只匹配 9 点。
    return value == int(field)


def cron_matches(cron_expr: str, moment: datetime) -> bool:
    """判断这一分钟是否符合计划，例如 "0 9 * * 1-5" 表示工作日 09:00。"""
    fields = cron_expr.split()
    if len(fields) != 5:
        return False

    minute, hour, day, month, weekday = fields

    # Python 的 weekday() 用 0 表示周一、6 表示周日。
    # 项目的 cron 用 0 表示周日、1 表示周一，所以先转换。
    cron_weekday = (moment.weekday() + 1) % 7

    # 分钟、小时、月份必须全部符合。任意一项不符就提前返回。
    if not (
        _cron_field_matches(minute, moment.minute)
        and _cron_field_matches(hour, moment.hour)
        and _cron_field_matches(month, moment.month)
    ):
        return False

    day_matches = _cron_field_matches(day, moment.day)
    weekday_matches = _cron_field_matches(weekday, cron_weekday)

    # 两项都是 *，说明日期和星期都不限。
    if day == "*" and weekday == "*":
        return True

    # 只有一项是 * 时，由另一项决定。
    if day == "*":
        return weekday_matches
    if weekday == "*":
        return day_matches

    # 两项都指定时，沿用原项目的规则：满足其中一项就可以触发。
    return day_matches or weekday_matches

def _validate_cron_field(
    field: str,
    minimum: int,
    maximum: int,
) -> str | None:
    """校验一个 cron 字段，通过时返回 None。"""

    # * 接受任意合法取值。
    if field == "*":
        return None

    # 匹配函数会执行 value % step，所以步长必须是正整数。
    if field.startswith("*/"):
        step = field[2:]
        if not step.isdecimal() or int(step) <= 0:
            return f"Invalid step: {field}"
        return None

    # 列表里的每一项都要合法。比如分钟字段 0,15,60，
    # 即使前两项有效，60 仍应让整个字段校验失败。
    if "," in field:
        for part in field.split(","):
            error = _validate_cron_field(part.strip(), minimum, maximum)
            if error is not None:
                return error
        return None

    if "-" in field:
        start, end = field.split("-", 1)

        # 先检查能否转成整数，再比较大小。
        if not start.isdecimal() or not end.isdecimal():
            return f"Invalid range: {field}"

        start_value, end_value = int(start), int(end)

        # 先确认区间方向正确，再确认两个端点都在允许范围内。
        if start_value > end_value:
            return f"Range start is greater than end: {field}"
        if start_value < minimum or end_value > maximum:
            return f"Range {field} is outside [{minimum}-{maximum}]"
        return None

    # 剩下的情况应该是单个数字。相同的数字在不同字段有不同限制，
    # 比如 25 可以用作分钟，却超出了小时的范围。
    if not field.isdecimal():
        return f"Invalid field: {field}"

    value = int(field)
    if not minimum <= value <= maximum:
        return f"Value {value} is outside [{minimum}-{maximum}]"

    return None

def validate_cron(cron_expr: str) -> str | None:
    """检查分钟、小时、日期、月份、星期五个字段，出错时带上字段名称。"""
    if not isinstance(cron_expr, str):
        return "Cron expression must be a string"
    fields = cron_expr.split()

    # 先确认正好有五项。zip() 遇到较短的一边就会停止，
    # 因此必须先检查数量，才不会漏掉多写或少写的字段。
    if len(fields) != 5:
        return f"Expected 5 fields, got {len(fields)}"

    field_rules = [
        ("minute", 0, 59),
        ("hour", 0, 23),
        ("day-of-month", 1, 31),
        ("month", 1, 12),
        ("day-of-week", 0, 6),
    ]

    # 将表达式的第一个字段与分钟规则配对，第二个与小时规则配对，
    # 依次检查。错误信息带上字段名，调用方就知道该改哪一项。
    for field, (name, minimum, maximum) in zip(fields, field_rules):
        error = _validate_cron_field(field, minimum, maximum)
        if error is not None:
            return f"{name}: {error}"

    return None

def save_durable_jobs() -> None:
    """把需要跨重启保留的任务写入 JSON 文件。"""
    with cron_lock:
        # asdict() 把 CronJob 转成字典，json.dumps() 才能序列化它。
        # durable=False 的任务只在当前进程里有效。
        payload = [
            asdict(job)
            for job in scheduled_jobs.values()
            if job.durable
        ]

        # 临时文件和正式文件放在同一个目录，便于完成后整体替换。
        # 进程 ID 和线程 ID 用来区分不同写入者的临时文件名。
        temporary = DURABLE_PATH.with_name(
            f"{DURABLE_PATH.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )

        try:
            # 先把完整的 JSON 写好。写到一半出错时，正式文件还是旧版本。
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, DURABLE_PATH)
        finally:
            # 替换成功时，临时路径已经消失。失败时，清理残留文件。
            temporary.unlink(missing_ok=True)

def new_cron_id() -> str:
    """分配一个尚未使用的 ID。调用方应持有 cron_lock。"""
    for _ in range(100):
        # token_hex(4) 把 4 个随机字节表示成 8 个十六进制字符。
        job_id = f"cron_{secrets.token_hex(4)}"
        if job_id not in scheduled_jobs:
            return job_id

    raise RuntimeError("Could not allocate a cron job ID")


def schedule_job(
    cron: str,
    prompt: str,
    recurring: bool = True,
    durable: bool = True,
) -> CronJob | str:
    """登记任务。需要持久化时，保存成功后才返回任务对象。"""
    error = validate_cron(cron)
    if error is not None:
        return error

    if not isinstance(prompt, str) or not prompt.strip():
        return "Prompt cannot be empty"
    if not isinstance(recurring, bool) or not isinstance(durable, bool):
        return "recurring and durable must be booleans"

    with cron_lock:
        job = CronJob(
            id=new_cron_id(),
            cron=cron,
            prompt=prompt,
            recurring=recurring,
            durable=durable,
        )
        scheduled_jobs[job.id] = job

        try:
            # 保存函数从 scheduled_jobs 取数据，所以要先登记再保存。
            if job.durable:
                save_durable_jobs()
        except Exception:
            # 磁盘没有保存成功，撤销这次内存登记，再把错误交给调用方。
            scheduled_jobs.pop(job.id, None)
            raise

    return job


def load_durable_jobs() -> None:
    """读取磁盘计划，并把等待交付的任务恢复到队列。"""
    with cron_lock:
        try:
            payload = json.loads(DURABLE_PATH.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise ValueError("Expected a JSON list")
        except FileNotFoundError:
            # 第一次启动时还没有保存过计划，直接从空表开始。
            return
        except (OSError, ValueError) as error:
            print(f"[cron] could not read saved jobs: {error}")
            return

        for item in payload:
            try:
                # **item 把 JSON 字典中的字段展开为 CronJob 的关键字参数。
                job = CronJob(**item)

                # 文件也可能被手工编辑，所以恢复时再检查一遍。
                if not isinstance(job.id, str) or not job.id.startswith("cron_"):
                    raise ValueError("Invalid job ID")
                if not isinstance(job.cron, str):
                    raise ValueError("Cron expression must be a string")
                if not isinstance(job.prompt, str) or not job.prompt.strip():
                    raise ValueError("Prompt cannot be empty")
                if not isinstance(job.recurring, bool) or not isinstance(
                    job.pending_delivery, bool
                ) or job.durable is not True:
                    raise ValueError("Invalid saved job flags")
                if job.last_fired is not None and not isinstance(job.last_fired, str):
                    raise ValueError("Invalid last_fired marker")

                error = validate_cron(job.cron)
                if error is not None:
                    raise ValueError(error)

            except (TypeError, ValueError) as error:
                # 一条记录有问题时，继续尝试恢复文件里的其他记录。
                print(f"[cron] skipped invalid saved job: {error}")
                continue

            # 同一个进程里重复调用时，已经存在的记录无需再加入。
            if job.id in scheduled_jobs:
                continue

            scheduled_jobs[job.id] = job
            if job.pending_delivery:
                cron_queue.append(job)

def _enqueue_due_job(job: CronJob, minute_marker: str) -> None:
    """记录一次触发并把任务放进队列。调用方须持有 cron_lock。"""

    # 先记住旧值。如果持久化失败，就可以撤销这次触发。
    old_pending = job.pending_delivery
    old_last_fired = job.last_fired

    job.pending_delivery = True
    job.last_fired = minute_marker

    try:
        if job.durable:
            # 保存的是修改后的任务状态。若程序此时退出，
            # 下次启动能凭 pending_delivery 把任务恢复进队列。
            save_durable_jobs()
    except Exception:
        job.pending_delivery = old_pending
        job.last_fired = old_last_fired
        raise

    # 磁盘保存成功后才发布到队列。纯内存任务可以直接到这一步。
    cron_queue.append(job)


def poll_due_jobs(moment: datetime) -> None:
    """扫描到点的计划，同一计划在同一分钟最多入队一次。"""
    minute_marker = moment.strftime("%Y-%m-%d %H:%M")

    with cron_lock:
        for job in scheduled_jobs.values():
            # 上次仍在交付，或这一分钟已经触发，都跳过。
            if job.pending_delivery or job.last_fired == minute_marker:
                continue
            try:
                if cron_matches(job.cron, moment):
                    _enqueue_due_job(job, minute_marker)
                    print(f"  [cron] due {job.id}: {job.prompt[:60]}")
            except Exception as error:
                print(f"  [cron] could not enqueue {job.id}: {error}")


def cancel_job(job_id: str) -> str:
    """取消计划，并移除它还在队列中的触发。"""
    with cron_lock:
        job = scheduled_jobs.pop(job_id, None)
        if job is None:
            return f"Job {job_id} not found"

        previous_queue = list(cron_queue)
        cron_queue[:] = [queued for queued in cron_queue if queued.id != job_id]
        try:
            if job.durable:
                save_durable_jobs()
        except Exception:
            scheduled_jobs[job_id] = job
            cron_queue[:] = previous_queue
            raise

    return f"Cancelled {job_id}"


def consume_cron_queue() -> list[CronJob]:
    """领走这一批任务。模型确认接收前，pending_delivery 仍为 True。"""
    with cron_lock:
        jobs = list(cron_queue)
        cron_queue.clear()
        return jobs


def restore_cron_jobs(jobs: list[CronJob]) -> None:
    """交付失败时重新排队，跳过已取消、已确认或已在队列中的任务。"""
    with cron_lock:
        queued_ids = {job.id for job in cron_queue}
        for delivered in jobs:
            job = scheduled_jobs.get(delivered.id)
            if job is not None and job.pending_delivery and job.id not in queued_ids:
                cron_queue.append(job)
                queued_ids.add(job.id)


def acknowledge_cron_jobs(jobs: list[CronJob]) -> None:
    """模型收到这批任务后，更新重复计划并移除一次性计划。"""
    with cron_lock:
        confirmed = []
        for delivered in jobs:
            job = scheduled_jobs.get(delivered.id)
            if job is None or not job.pending_delivery:
                continue

            confirmed.append(job)
            job.pending_delivery = False
            if not job.recurring:
                scheduled_jobs.pop(job.id)

        try:
            if any(job.durable for job in confirmed):
                save_durable_jobs()
        except Exception:
            # 保存失败时恢复待交付状态，下一次还能重试。
            for job in confirmed:
                job.pending_delivery = True
                scheduled_jobs[job.id] = job
            restore_cron_jobs(confirmed)
            raise
