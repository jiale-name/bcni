"""只读探针：核对导入任务 / 文件 / Agent 任务的实际落库状态（排查 PARSE_INTERRUPTED 与 0/0 计数）。

⚠️ 本脚本**只读**，不做任何写入。
"""
import json

import pymysql

CONN = dict(
    host="127.0.0.1",
    port=3306,
    user="root",
    password="123456",
    database="talent_platform_v2",
    charset="utf8mb4",
)


def main() -> None:
    conn = pymysql.connect(**CONN)
    cur = conn.cursor()

    print("=== import_task (today) ===")
    cur.execute(
        "select id, task_no, status, total_count, success_count, fail_count, created_at, finished_at "
        "from import_task where task_no like 'IMP20260924%' order by id"
    )
    tasks = cur.fetchall()
    for r in tasks:
        print(r)

    for tid, tno, *_ in tasks:
        print(f"\n=== import_file of {tno} (task_id={tid}) ===")
        cur.execute(
            "select id, resume_id, file_name, file_ext, parse_status, parse_error, agent_task_id, ocr_used "
            "from import_file where task_id=%s",
            (tid,),
        )
        for r in cur.fetchall():
            print(r)

    print("\n=== agent_task (latest 6) with graph_state keys ===")
    cur.execute("select id, biz_id, status, current_node, graph_state from agent_task order by id desc limit 6")
    for aid, biz, status, node, gs in cur.fetchall():
        try:
            state = json.loads(gs) if gs else {}
        except Exception:
            state = {"__unparsable__": str(gs)[:80]}
        print(f"agent_task={aid} biz={biz} status={status} node={node}")
        print("   keys:", sorted(state.keys()))
        for k in ("text_len", "format", "needs_ocr", "fields", "extracted", "confidence", "reason"):
            if k in state:
                v = state[k]
                print(f"   {k} =", str(v)[:200])
        txt = state.get("text")
        if isinstance(txt, str):
            print("   text_len =", len(txt), "head =", repr(txt[:120]))

    print("\n=== agent_step of the two failed parse tasks ===")
    cur.execute(
        "select task_id, step_index, node_name, status, latency_ms, left(coalesce(error_msg,''),120) "
        "from agent_step where task_id in (361442866440048640, 361443715019051008) order by task_id, step_index"
    )
    for r in cur.fetchall():
        print(r)

    print("\n=== agent_interrupt of the two failed parse tasks ===")
    cur.execute(
        "select task_id, node_name, interrupt_type, status, left(coalesce(payload_json,''),400) "
        "from agent_interrupt where task_id in (361442866440048640, 361443715019051008)"
    )
    for r in cur.fetchall():
        print(r)

    conn.close()


if __name__ == "__main__":
    main()
