"""
fxi.index_retrieval.project_lexicon - 小说专有实体分词词典动态管理
"""

from pathlib import Path
from typing import Optional
import jieba

from fxi.core.config import FxiConfig, load_config
from fxi.storage.sqlite_client import DatabaseClient


class LexiconManager:
    """专有词表导出与动态加载管理器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()

    def export_lexicon(self, project_id: Optional[str] = None) -> Path:
        """
        从 SQLite 的 entities、item_prototypes 表中导出实体、势力和物品名称
        写入 data/project_lexicon.txt，并通知 jieba 加载
        """
        dict_path = self.config.jieba_custom_dict_path
        dict_path.parent.mkdir(parents=True, exist_ok=True)

        client = DatabaseClient(self.config.sqlite_path)
        words: set[str] = set()

        with client.get_connection() as conn:
            query = "SELECT name, aliases_json FROM entities"
            params = []
            if project_id:
                query += " WHERE work_id = ?"
                params.append(project_id)

            cur = conn.execute(query, params)
            for row in cur.fetchall():
                name = row["name"]
                if name and len(name) >= 2:
                    words.add(name)

            # 导出物品模板
            proto_query = "SELECT name FROM item_prototypes"
            if project_id:
                proto_query += " WHERE work_id = ?"
            cur = conn.execute(proto_query, params)
            for row in cur.fetchall():
                pname = row["name"]
                if pname and len(pname) >= 2:
                    words.add(pname)

        # 写入词典文件 (每行: 词条 频次 词性)
        lines = [f"{w} 1000 n" for w in sorted(words)]
        dict_path.write_text("\n".join(lines), encoding="utf-8")

        # 重新加载至 jieba
        if dict_path.is_file() and dict_path.stat().st_size > 0:
            jieba.load_userdict(str(dict_path))

        return dict_path

    def ensure_loaded(self) -> None:
        """确保词典已加载"""
        dict_path = self.config.jieba_custom_dict_path
        if dict_path.is_file() and dict_path.stat().st_size > 0:
            jieba.load_userdict(str(dict_path))
