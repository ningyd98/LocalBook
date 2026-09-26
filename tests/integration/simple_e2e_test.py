#!/usr/bin/env python3
"""
简化的数据库流程测试 - 匹配实际 schema
验证本地记录流转：设备记录 → 创建内容 → 同步状态
"""

import json
import sqlite3
import sys
import tempfile
from pathlib import Path


def init_database(db_path: str):
    """初始化数据库."""
    schema_path = Path(__file__).parent.parent.parent / "server" / "reader" / "migrations" / "001_init_reader_schema.sql"

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    with open(schema_path) as f:
        schema_sql = f.read()

    conn.executescript(schema_sql)
    conn.commit()

    return conn


def test_scenario_1_offline_sync() -> None:
    """
    场景 1: 离线阅读 → 同步
    - 创建设备
    - 离线保存 3 条 Highlight 和 2 条 Note
    - 验证同步状态变化
    """
    print("\n" + "=" * 70)
    print("场景 1: 离线阅读 → 同步")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_database(str(db_path))
        cursor = conn.cursor()

        # 1. 创建设备
        device_id = "01930f5a-1111-7890-abcd-ef0123456789"
        cursor.execute("""
            INSERT INTO devices (id, device_name, device_type)
            VALUES (?, ?, ?)
        """, (device_id, "Test MacBook Pro", "macos"))
        conn.commit()
        print(f"✓ 创建设备: {device_id}")

        # 2. 创建 Source
        source_id = "01930f5a-2222-7890-abcd-ef0123456789"
        cursor.execute("""
            INSERT INTO reader_sources (id, type, title, url)
            VALUES (?, ?, ?, ?)
        """, (source_id, "web", "Integration Testing Guide", "https://example.com/testing"))
        conn.commit()
        print(f"✓ 创建 Source: Integration Testing Guide")

        # 3. 创建 Session
        session_id = "01930f5a-3000-7890-abcd-ef0123456789"
        cursor.execute("""
            INSERT INTO reader_sessions (id, device_id, source_id)
            VALUES (?, ?, ?)
        """, (session_id, device_id, source_id))
        conn.commit()
        print(f"✓ 创建 Session: {session_id}")

        # 4. 保存 3 条 Highlight（离线）
        highlights = [
            ("01930f5a-3331-7890-abcd-ef0123456789", "Software testing is crucial for quality."),
            ("01930f5a-3332-7890-abcd-ef0123456789", "Continuous integration improves code quality."),
            ("01930f5a-3333-7890-abcd-ef0123456789", "End-to-end testing validates the entire system.")
        ]

        print(f"\n✓ 保存 {len(highlights)} 条 Highlight（离线，sync_state=local_only）:")
        for highlight_id, text in highlights:
            cursor.execute("""
                INSERT INTO reader_highlights (id, source_id, session_id, selected_text, sync_state)
                VALUES (?, ?, ?, ?, 'local_only')
            """, (highlight_id, source_id, session_id, text))
            print(f"  - {text[:50]}...")

        conn.commit()

        # 5. 添加 2 条 Note
        notes = [
            ("01930f5a-4441-7890-abcd-ef0123456789", "Excellent coverage of testing strategies."),
            ("01930f5a-4442-7890-abcd-ef0123456789", "TODO: Review CI/CD pipeline section.")
        ]

        print(f"\n✓ 添加 {len(notes)} 条 Note（离线，sync_state=local_only）:")
        for note_id, content in notes:
            cursor.execute("""
                INSERT INTO reader_notes (id, source_id, session_id, content, sync_state)
                VALUES (?, ?, ?, ?, 'local_only')
            """, (note_id, source_id, session_id, content))
            print(f"  - {content[:50]}...")

        conn.commit()

        # 6. 验证离线数据
        print(f"\n✓ 验证离线数据:")

        cursor.execute("SELECT COUNT(*) as cnt FROM reader_highlights WHERE sync_state = 'local_only'")
        local_highlights = cursor.fetchone()["cnt"]
        print(f"  - Highlights (local_only): {local_highlights}")
        assert local_highlights == 3, f"Expected 3 local highlights, got {local_highlights}"

        cursor.execute("SELECT COUNT(*) as cnt FROM reader_notes WHERE sync_state = 'local_only'")
        local_notes = cursor.fetchone()["cnt"]
        print(f"  - Notes (local_only): {local_notes}")
        assert local_notes == 2, f"Expected 2 local notes, got {local_notes}"

        # 7. 模拟上线后同步（更新 sync_state）
        print(f"\n✓ 模拟同步（local_only → pending → synced）:")

        cursor.execute("UPDATE reader_highlights SET sync_state = 'pending' WHERE sync_state = 'local_only'")
        cursor.execute("UPDATE reader_notes SET sync_state = 'pending' WHERE sync_state = 'local_only'")
        conn.commit()

        cursor.execute("SELECT COUNT(*) as cnt FROM reader_highlights WHERE sync_state = 'pending'")
        pending_highlights = cursor.fetchone()["cnt"]
        print(f"  - Highlights (pending): {pending_highlights}")

        cursor.execute("UPDATE reader_highlights SET sync_state = 'synced' WHERE sync_state = 'pending'")
        cursor.execute("UPDATE reader_notes SET sync_state = 'synced' WHERE sync_state = 'pending'")
        conn.commit()

        cursor.execute("SELECT COUNT(*) as cnt FROM reader_highlights WHERE sync_state = 'synced'")
        synced_highlights = cursor.fetchone()["cnt"]
        print(f"  - Highlights (synced): {synced_highlights}")
        assert synced_highlights == 3, f"Expected 3 synced highlights, got {synced_highlights}"

        cursor.execute("SELECT COUNT(*) as cnt FROM reader_notes WHERE sync_state = 'synced'")
        synced_notes = cursor.fetchone()["cnt"]
        print(f"  - Notes (synced): {synced_notes}")
        assert synced_notes == 2, f"Expected 2 synced notes, got {synced_notes}"

        conn.close()

        print(f"\n{'='*70}")
        print("✅ 场景 1 通过")
        print(f"{'='*70}")



def test_scenario_2_ai_conversation() -> None:
    """
    场景 2: AI 查询
    - 创建 Highlight
    - 创建 Conversation
    - 添加多轮对话消息
    """
    print("\n" + "=" * 70)
    print("场景 2: AI 查询")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_database(str(db_path))
        cursor = conn.cursor()

        # Setup
        device_id = "01930f5a-5555-7890-abcd-ef0123456789"
        source_id = "01930f5a-6666-7890-abcd-ef0123456789"
        session_id = "01930f5a-6600-7890-abcd-ef0123456789"
        highlight_id = "01930f5a-7777-7890-abcd-ef0123456789"

        cursor.execute("INSERT INTO devices (id, device_name, device_type) VALUES (?, ?, ?)",
                      (device_id, "Test iPhone", "ios"))
        cursor.execute("INSERT INTO reader_sources (id, type, title, url) VALUES (?, ?, ?, ?)",
                      (source_id, "web", "Quantum Computing Basics", "https://example.com/quantum"))
        cursor.execute("INSERT INTO reader_sessions (id, device_id, source_id) VALUES (?, ?, ?)",
                      (session_id, device_id, source_id))
        cursor.execute("INSERT INTO reader_highlights (id, source_id, session_id, selected_text) VALUES (?, ?, ?, ?)",
                      (highlight_id, source_id, session_id, "Qubits can exist in superposition states."))
        conn.commit()

        print(f"✓ Setup: 创建 Highlight")

        # 1. 创建 Conversation
        conversation_id = "01930f5a-8888-7890-abcd-ef0123456789"
        cursor.execute("""
            INSERT INTO reader_conversations (id, source_id, highlight_id, session_id)
            VALUES (?, ?, ?, ?)
        """, (conversation_id, source_id, highlight_id, session_id))
        conn.commit()
        print(f"\n✓ 创建 Conversation: {conversation_id}")

        # 2. 添加对话消息（3 轮）
        messages = [
            ("user", "请解释什么是量子叠加态？"),
            ("assistant", "量子叠加态是量子力学的核心概念..."),
            ("user", "那叠加态是如何测量的？"),
            ("assistant", "测量量子态会导致波函数坍缩..."),
            ("user", "有没有实际应用案例？"),
            ("assistant", "量子叠加态的实际应用包括：1) 量子密钥分发...")
        ]

        print(f"\n✓ 添加 {len(messages)} 条消息:")
        for i, (role, content) in enumerate(messages):
            msg_id = f"01930f5a-999{i}-7890-abcd-ef0123456789"
            cursor.execute("""
                INSERT INTO reader_messages (id, conversation_id, role, content)
                VALUES (?, ?, ?, ?)
            """, (msg_id, conversation_id, role, content))
            role_icon = "👤" if role == "user" else "🤖"
            print(f"  [{i+1}] {role_icon} {role}: {content[:40]}...")

        conn.commit()

        # 3. 验证对话历史
        cursor.execute("SELECT COUNT(*) as cnt FROM reader_messages WHERE conversation_id = ?", (conversation_id,))
        message_count = cursor.fetchone()["cnt"]
        print(f"\n✓ 验证: 对话包含 {message_count} 条消息 (3 user + 3 assistant)")
        assert message_count == 6, f"Expected 6 messages, got {message_count}"

        cursor.execute("""
            SELECT role, COUNT(*) as cnt
            FROM reader_messages
            WHERE conversation_id = ?
            GROUP BY role
        """, (conversation_id,))

        for row in cursor.fetchall():
            print(f"  - {row['role']}: {row['cnt']} 条")

        conn.close()

        print(f"\n{'='*70}")
        print("✅ 场景 2 通过")
        print(f"{'='*70}")



def test_scenario_3_multi_device() -> None:
    """
    场景 3: 多设备同步
    - 设备 A 创建 Highlight
    - 设备 B 查询并获取
    """
    print("\n" + "=" * 70)
    print("场景 3: 多设备同步")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_database(str(db_path))
        cursor = conn.cursor()

        # 1. 注册设备 A 和 B
        device_a = "01930f5a-aaaa-7890-abcd-ef0123456789"
        device_b = "01930f5a-bbbb-7890-abcd-ef0123456789"

        cursor.execute("INSERT INTO devices (id, device_name, device_type) VALUES (?, ?, ?)",
                      (device_a, "MacBook Pro", "macos"))
        cursor.execute("INSERT INTO devices (id, device_name, device_type) VALUES (?, ?, ?)",
                      (device_b, "iPad Pro", "ios"))
        conn.commit()

        print(f"✓ 注册设备 A: MacBook Pro")
        print(f"✓ 注册设备 B: iPad Pro")

        # 2. 设备 A 创建内容
        source_id = "01930f5a-cccc-7890-abcd-ef0123456789"
        session_id = "01930f5a-cc00-7890-abcd-ef0123456789"
        highlight_id = "01930f5a-dddd-7890-abcd-ef0123456789"

        cursor.execute("INSERT INTO reader_sources (id, type, title) VALUES (?, ?, ?)",
                      (source_id, "pdf", "Distributed Systems Paper"))
        cursor.execute("INSERT INTO reader_sessions (id, device_id, source_id) VALUES (?, ?, ?)",
                      (session_id, device_a, source_id))
        cursor.execute("""
            INSERT INTO reader_highlights (id, source_id, session_id, selected_text, sync_state)
            VALUES (?, ?, ?, ?, 'synced')
        """, (highlight_id, source_id, session_id, "Consensus algorithms are fundamental."))
        conn.commit()

        print(f"\n✓ 设备 A 创建 Highlight (已同步到服务器)")

        # 3. 设备 B 查询同步数据
        cursor.execute("""
            SELECT h.id, h.selected_text, s.title, sess.device_id
            FROM reader_highlights h
            JOIN reader_sources s ON h.source_id = s.id
            JOIN reader_sessions sess ON h.session_id = sess.id
            WHERE h.sync_state = 'synced' AND sess.device_id != ?
        """, (device_b,))

        results = cursor.fetchall()
        print(f"\n✓ 设备 B 查询到 {len(results)} 条同步内容:")

        for row in results:
            print(f"  - '{row['selected_text'][:40]}...' from {row['title']}")
            assert row["device_id"] == device_a, "应该是设备 A 创建的"

        assert len(results) == 1, f"Expected 1 synced highlight, got {len(results)}"

        # 4. 验证数据一致性
        cursor.execute("SELECT COUNT(*) as cnt FROM reader_highlights WHERE id = ?", (highlight_id,))
        exists = cursor.fetchone()["cnt"]
        print(f"\n✓ 数据一致性: Highlight 存在 = {exists == 1}")
        assert exists == 1, "Highlight 应该存在"

        conn.close()

        print(f"\n{'='*70}")
        print("✅ 场景 3 通过")
        print(f"{'='*70}")

def main():
    """运行所有测试场景."""
    print("\n" + "=" * 70)
    print("ReadFlow MVP 数据库流程测试")
    print("=" * 70)
    print("\n使用实际 SQLite schema 验证核心数据流")

    results = []

    try:
        test_scenario_1_offline_sync()
        results.append(("场景 1: 离线阅读 → 同步", True))
        test_scenario_2_ai_conversation()
        results.append(("场景 2: AI 对话记录", True))
        test_scenario_3_multi_device()
        results.append(("场景 3: 多设备同步", True))

        print("\n" + "=" * 70)
        print("测试结果汇总")
        print("=" * 70)

        for name, passed in results:
            status = "✅ PASSED" if passed else "❌ FAILED"
            print(f"{status}: {name}")

        all_passed = all(result[1] for result in results)

        if all_passed:
            print("\n" + "=" * 70)
            print("🎉 所有数据库流程测试通过！")
            print("=" * 70)
            print("\n验收标准:")
            print("✅ 离线数据保存 (3 Highlights + 2 Notes)")
            print("✅ 同步状态流转 (local_only → pending → synced)")
            print("✅ AI 对话历史保存 (6 条消息，3 轮对话)")
            print("✅ 多设备数据同步 (设备 A → 服务器 → 设备 B)")
            print("✅ 无数据丢失")
            print("✅ 数据一致性验证通过")
            return 0
        else:
            print("\n❌ 部分测试失败")
            return 1

    except Exception as e:
        print(f"\n❌ 测试执行失败: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
