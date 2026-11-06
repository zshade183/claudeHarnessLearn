"""
技能加载器
解析技能定义文本，提取元数据和主体内容。
约定：每个技能是 <skills_dir>/<技能名>/SKILL.md 一个文件。
"""
import yaml
from pathlib import Path
from agent.config import SKILLS_DIR


class SkillLoader:
    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        # 技能名 -> {"name", "description", "content"}；构造时立即扫描一次
        self.skills: dict[str, dict[str, str]] = {}
        self.scan()

    @staticmethod  # 静态方法，无需实例化
    def parse_frontmatter(text: str) -> tuple[dict, str]:
        """
        解析技能定义文本，提取元数据和主体内容。
        :param text: 技能定义文本
        :return: 元数据字典和主体内容字符串
        """
        lines = text.splitlines(keepends=True)  # 保留换行符，方便后续处理

        if not lines or lines[0].rstrip("\r\n") != "---":  # 检查是否以 "---" 开头
            return {}, text  # 如果不是 "---" 开头，返回空元数据和原始文本

        # 在首行之后查找第二个 "---"，它就是 frontmatter 的结束分隔符
        closing_index = next(
            (
                index
                for index, line in enumerate(lines[1:], start=1)
                if line.rstrip("\r\n") == "---"
            ),
            None,
        )

        if closing_index is None:  # 头部没有闭合，视为没有 frontmatter
            return {}, text

        frontmatter = "".join(lines[1:closing_index])  # 两个分隔符之间的 YAML
        body = "".join(lines[closing_index + 1:]).strip()  # 分隔符之后的正文

        try:
            metadata = yaml.safe_load(frontmatter) or {}
        except yaml.YAMLError:  # YAML 语法错误时退化为空元数据
            metadata = {}

        if not isinstance(metadata, dict):  # 保证始终返回 dict，调用方无需判空
            metadata = {}

        return metadata, body

    def scan(self):
        """
        扫描技能目录，加载所有技能定义。
        :return: None
        """
        self.skills.clear()  # 清空已加载的技能定义

        if not self.skills_dir.exists():  # 检查技能目录是否存在
            return

        skills_root = self.skills_dir.resolve()  # 解析为绝对路径，供下面的越界校验使用

        # 每个技能位于一个子目录的 SKILL.md；排序保证加载顺序稳定
        for manifest in sorted(self.skills_dir.glob("*/SKILL.md")):
            # 跳过非普通文件、以及软链接指向技能目录之外的文件
            if (
                not manifest.is_file()
                or not manifest.resolve().is_relative_to(skills_root)
            ):
                continue

            content = manifest.read_text(encoding="utf-8")
            metadata, body = self.parse_frontmatter(content)

            # name 优先取 frontmatter，缺失则回退为目录名
            raw_name = metadata.get("name")
            name = raw_name.strip() if isinstance(raw_name, str) else ""
            name = name or manifest.parent.name

            raw_description = metadata.get("description")
            description = (
                raw_description.strip()
                if isinstance(raw_description, str)
                else ""
            )
            # description 缺失则回退为正文首行
            description = description or body.split("\n", 1)[0]
            # 去掉标题符号并压成单行，便于塞进 catalog 里逐个列出
            description = " ".join(
                str(description).lstrip("# ").split()
            )

            self.skills[name] = {
                "name": name,
                "description": description,
                "content": content,
            }

    def catalog(self) -> str:
        """
        返回技能清单（名称 + 描述），供模型挑选要加载哪个技能。
        """
        if not self.skills:
            return "(no skills found)"

        return "\n".join(
            f"- {skill['name']}: {skill['description']}"
            for skill in self.skills.values()
        )

    def load(self, name: str) -> str:
        """
        按名称返回技能的完整正文；找不到时返回带可用列表的错误信息。
        """
        skill = self.skills.get(name)

        if skill:
            return skill["content"]

        available = ", ".join(self.skills) or "none"
        return (
            f"Error: Unknown skill '{name}'. "
            f"Available: {available}"
        )
SKILL_LOADER = SkillLoader(SKILLS_DIR)