#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""配置迁移模块：自动将旧配置文件升级到最新格式"""

import configparser
import logging


MIGRATION_MARKER = '__migrations__'

MIGRATIONS = [
    {
        'id': 1,
        'description': '重命名 [GitHub].release_version → [GitHub].m9a_update_channel',
        'apply': lambda cfg, _: _migrate_1_rename_release_version(cfg),
    },
]


def apply_migrations(config: configparser.ConfigParser, config_file: str,
                     logger: logging.Logger) -> bool:
    """
    应用所有待处理的迁移，如有变更则写回配置文件

    Returns:
        bool: 是否执行了迁移
    """
    applied = _get_applied_migrations(config)
    changed = False

    for migration in MIGRATIONS:
        mid = migration['id']
        if mid in applied:
            continue
        desc = migration.get('description', f'#{mid}')
        logger.info(f"正在应用配置迁移 [{mid}]: {desc}")
        try:
            migration['apply'](config, logger)
            _mark_applied(config, mid)
            applied.add(mid)
            changed = True
            logger.info(f"配置迁移完成")
        except Exception as e:
            logger.warning(f"配置迁移 [{mid}] 失败: {e}")

    if changed:
        _remove_marker_section(config)
        if applied:
            config.add_section(MIGRATION_MARKER)
            for mid in sorted(applied):
                config.set(MIGRATION_MARKER, str(mid), 'done')
        try:
            with open(config_file, 'w', encoding='utf-8') as f:
                config.write(f)
        except OSError as e:
            logger.error(f"写入迁移后的配置文件失败: {e}")

    return changed


def _get_applied_migrations(config: configparser.ConfigParser) -> set:
    if config.has_section(MIGRATION_MARKER):
        return {int(k) for k, v in config.items(MIGRATION_MARKER) if v == 'done'}
    return set()


def _mark_applied(config: configparser.ConfigParser, migration_id: int) -> None:
    if not config.has_section(MIGRATION_MARKER):
        config.add_section(MIGRATION_MARKER)
    config.set(MIGRATION_MARKER, str(migration_id), 'done')


def _remove_marker_section(config: configparser.ConfigParser) -> None:
    if config.has_section(MIGRATION_MARKER):
        config.remove_section(MIGRATION_MARKER)


def _migrate_1_rename_release_version(config: configparser.ConfigParser) -> None:
    """[GitHub].release_version → [GitHub].m9a_update_channel"""
    if not config.has_section('GitHub'):
        return
    if config.has_option('GitHub', 'release_version'):
        old_val = config.get('GitHub', 'release_version')
        if not config.has_option('GitHub', 'm9a_update_channel'):
            config.set('GitHub', 'm9a_update_channel', old_val)
        config.remove_option('GitHub', 'release_version')
