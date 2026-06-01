#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""配置迁移模块：自动将旧配置文件升级到最新格式"""

import configparser
import logging


MIGRATION_MARKER = '__migrations__'

MIGRATIONS = [
    {
        'id': 1, # 迁移 ID
        'type': 'rename_key',            # 重命名键
        'section': 'GitHub',             # 目标节
        'old_key': 'release_version',    # 旧键
        'new_key': 'm9a_update_channel', # 新键
        'description': '重命名 [GitHub].release_version → [GitHub].m9a_update_channel', # 日志输出描述
    },
]

def _apply_rename_key(config: configparser.ConfigParser,
                       section: str, old_key: str, new_key: str) -> bool:
    """通用键重命名：section 下 old_key → new_key，保留值"""
    if not config.has_section(section):
        return False
    if not config.has_option(section, old_key):
        return False
    old_val = config.get(section, old_key)
    if not config.has_option(section, new_key):
        config.set(section, new_key, old_val)
    config.remove_option(section, old_key)
    return True


MIGRATION_HANDLERS = {
    'rename_key': _apply_rename_key,
}


def apply_migrations(config: configparser.ConfigParser,
                     logger: logging.Logger) -> bool:
    """
    在内存中应用所有待处理的迁移

    Returns:
        bool: 是否执行了迁移（调用方据此触发文件重建）
    """
    applied = _get_applied_migrations(config)
    changed = False

    for migration in MIGRATIONS:
        mid = migration['id']
        if mid in applied:
            continue
        try:
            handler = MIGRATION_HANDLERS[migration['type']]
            if not handler(config, **{k: v for k, v in migration.items()
                                       if k not in ('id', 'type', 'description')}):
                continue
            desc = migration.get('description', f'#{mid}')
            logger.info(f"检测到需要迁移 [{mid}]: {desc}")
            _mark_applied(config, mid)
            applied.add(mid)
            changed = True
            logger.info(f"配置迁移 [{mid}] 完成")
        except Exception as e:
            logger.warning(f"配置迁移 [{mid}] 失败: {e}")

    return changed


def _get_applied_migrations(config: configparser.ConfigParser) -> set:
    if config.has_section(MIGRATION_MARKER):
        return {int(k) for k, v in config.items(MIGRATION_MARKER) if v == 'done'}
    return set()


def _mark_applied(config: configparser.ConfigParser, migration_id: int) -> None:
    if not config.has_section(MIGRATION_MARKER):
        config.add_section(MIGRATION_MARKER)
    config.set(MIGRATION_MARKER, str(migration_id), 'done')
