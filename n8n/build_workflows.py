"""n8n 워크플로우 JSON(n8n/workflows/*.json)을 만든다. JSON 을 손으로 고치지 말고 이 파일을 고친 뒤 다시 실행한다.

  uv run python n8n/build_workflows.py

n8n 에서 가져오기: Workflows -> Import from File. 가져온 뒤 `결과 정리`·`요약` 노드에 알림 노드(Slack·Discord·이메일)를 연결한다.
"""
import json
import uuid
from pathlib import Path


def node(name, type_, params, pos, version=1, **extra):
    return {"id": str(uuid.uuid5(uuid.NAMESPACE_DNS, name)), "name": name, "type": type_, "typeVersion": version,
            "position": pos, "parameters": params, **extra}


HEAD = {"sendHeaders": True, "headerParameters": {"parameters": [{"name": "X-API-Key", "value": "={{ $env.PIPELINE_API_TOKEN }}"}]}}


def http(name, method, path, pos, body=None, timeout=120000):
    p = {"method": method, "url": "={{ $env.PIPELINE_API_URL }}" + path, **HEAD,
         "options": {"timeout": timeout, "response": {"response": {"neverError": True}}}}  # 409(작업 중) 등도 오류로 멈추지 않고 본문을 받는다
    if body is not None:
        p.update({"sendBody": True, "specifyBody": "json", "jsonBody": body})
    return node(name, "n8n-nodes-base.httpRequest", p, pos, 4.2)


def if_bool(name, expr, pos):
    return node(name, "n8n-nodes-base.if", {"conditions": {
        "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
        "conditions": [{"id": str(uuid.uuid5(uuid.NAMESPACE_DNS, name + "c")), "leftValue": expr, "rightValue": "",
                        "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
        "combinator": "and"}, "options": {}}, pos, 2.2)


def wait(name, seconds, pos):
    return node(name, "n8n-nodes-base.wait", {"resume": "timeInterval", "amount": seconds, "unit": "seconds"}, pos, 1.1,
                webhookId=str(uuid.uuid5(uuid.NAMESPACE_DNS, name + "w")))


def code(name, js, pos):
    return node(name, "n8n-nodes-base.code", {"jsCode": js}, pos, 2)


NOTIFY_TO = "nsa29047@gmail.com"   # 알림 받을 주소. 바꾸려면 여기를 고치고 다시 생성하거나 n8n 노드에서 직접 고친다


def email(name, pos):
    """SMTP 메일 발송. 앞 노드의 `subject`·`message` 를 보낸다. SMTP 자격 증명(credential)은 n8n 화면에서 노드마다 선택한다."""
    return node(name, "n8n-nodes-base.emailSend", {
        "fromEmail": NOTIFY_TO, "toEmail": NOTIFY_TO, "subject": "={{ $json.subject }}", "emailFormat": "text",
        "text": "={{ $json.message }}", "options": {}}, pos, 2.1)


def sticky(title, text, pos, w=420, h=200):
    return node("메모 " + title, "n8n-nodes-base.stickyNote", {"content": text, "width": w, "height": h}, pos, 1)


def conn(*pairs):
    c = {}
    for src, out, dst in pairs:
        c.setdefault(src, {"main": []})
        while len(c[src]["main"]) <= out:
            c[src]["main"].append([])
        c[src]["main"][out].append({"node": dst, "type": "main", "index": 0})
    return c


# ------------------------------------------------------------------ A. 새 영상 처리
RESULT_JS = r"""// 방금 끝난 작업의 결과를 영상별 메시지로 정리한다. 이 노드 뒤에 알림 노드(Slack·Discord·이메일)를 연결해 쓴다.
const j = $input.first().json;
const results = (j.result && j.result.results) || [];
if (results.length === 0) {
  return [{ json: { status: 'error', subject: '[파이프라인] ❌ 작업 결과를 읽지 못함',
    message: `작업 결과를 읽지 못했습니다 (exit ${j.exit_code}): ${(j.output_tail || []).join(' / ')}` } }];
}
const icon = { loaded: '✅', skipped: '⏭️', needs_review: '👀', failed: '❌', pending: '⏸️' };
return results.map(v => ({ json: { ...v,
  subject: `[파이프라인] ${icon[v.status] || ''} ${v.status} - ${v.title || v.video_id}`,
  message: `${icon[v.status] || ''} [${v.status}] ${v.title || v.video_id}\n단계: ${v.stage || '-'} / 자막: ${v.caption_type || '-'}` +
           (v.skip_reason ? `\n사유: ${v.skip_reason}` : '') + (v.error ? `\n오류: ${v.error}` : '') + (v.note ? `\n참고: ${v.note}` : '') +
           (v.michelin_pending ? '\n미슐랭: Parse 한도로 아직 못 채움 (매일 미슐랭 워크플로우가 채움)' : '') } }));"""

SUMMARY_JS = r"""// 처리할 영상이 더 없을 때 한 번: 사람이 볼 것들을 요약한다. 이 노드 뒤에 알림 노드를 연결해 쓴다.
const s = $input.first().json;
const att = s.attention || [];
const lines = att.map(v => `- ${v.status}${v.michelin_pending ? '+미슐랭대기' : ''} ${v.video_id} ${v.title || ''} ${v.skip_reason || v.error || ''}`);
if (att.length === 0) return [];   // 확인할 게 없으면 메일을 보내지 않는다(6시간마다 돌아도 조용하게)
return [{ json: { counts: s.counts, attention: att,
  subject: `[파이프라인] 확인 필요 ${att.length}건`,
  message: `파이프라인 한 바퀴 끝. 상태: ${JSON.stringify(s.counts)}` + (lines.length ? `\n확인 필요:\n${lines.join('\n')}` : '') } }];"""

NOTE_A = ("## 새 영상 자동 처리\n6시간마다 채널의 새 영상을 큐에 넣고, 영상을 하나씩 끝까지 처리한다(영상당 3~20분).\n\n"
          "**알림(메일)**: 영상 하나가 끝날 때마다 `영상 결과 메일`, 한 바퀴가 끝나고 확인할 영상이 있을 때만 `요약 메일`이 간다. 두 메일 노드와 `SMTP` 자격 증명을 n8n에서 한 번 선택해야 한다(받는 주소는 `NOTIFY_TO`).\n\n"
          "사람이 봐야 하는 영상(needs_review)은 API `POST /videos/{id}/reset` (accept / skip / redo_from) 로 되돌립니다.")

A = {
    "name": "새 영상 자동 처리",
    "nodes": [
        node("6시간마다", "n8n-nodes-base.scheduleTrigger", {"rule": {"interval": [{"field": "hours", "hoursInterval": 6}]}}, [0, 0], 1.2),
        node("수동 실행", "n8n-nodes-base.manualTrigger", {}, [0, 160], 1),
        http("새 영상 찾기", "POST", "/discover", [260, 80], '={{ JSON.stringify({recent: 10}) }}', 180000),
        http("다음 영상 처리 시작", "POST", "/run", [520, 80], "{}"),
        if_bool("시작됐나?", "={{ $json.started }}", [780, 80]),
        wait("60초 대기", 60, [1040, 0]),
        http("작업 상태", "GET", "/job", [1300, 0]),
        if_bool("아직 실행 중?", "={{ $json.running }}", [1560, 0]),
        code("결과 정리", RESULT_JS, [1820, 100]),
        http("현재 상태", "GET", "/status", [1040, 240]),
        code("요약", SUMMARY_JS, [1300, 240]),
        email("영상 결과 메일", [2080, 100]),
        email("요약 메일", [1560, 240]),
        sticky("A", NOTE_A, [260, -260], 640, 240),
    ],
    "connections": conn(
        ("6시간마다", 0, "새 영상 찾기"), ("수동 실행", 0, "새 영상 찾기"), ("새 영상 찾기", 0, "다음 영상 처리 시작"),
        ("다음 영상 처리 시작", 0, "시작됐나?"), ("시작됐나?", 0, "60초 대기"), ("시작됐나?", 1, "현재 상태"),
        ("60초 대기", 0, "작업 상태"), ("작업 상태", 0, "아직 실행 중?"), ("아직 실행 중?", 0, "60초 대기"),
        ("아직 실행 중?", 1, "결과 정리"), ("결과 정리", 0, "다음 영상 처리 시작"), ("결과 정리", 0, "영상 결과 메일"),
        ("현재 상태", 0, "요약"), ("요약", 0, "요약 메일")),
    "settings": {"executionOrder": "v1", "timezone": "Asia/Seoul"},
}

# ------------------------------------------------------------------ B. 미슐랭 채우기
MICH_JS = r"""// 미슐랭 채우기 작업 결과를 정리한다. 이 노드 뒤에 알림 노드를 연결해 쓴다.
const j = $input.first().json;
const r = j.result || {};
const resolved = r.resolved || [], pending = r.still_pending || [];
if (resolved.length === 0 && pending.length === 0 && !j.exit_code) return [];   // 바뀐 게 없으면 메일을 보내지 않는다
return [{ json: { resolved, still_pending: pending,
  subject: `[파이프라인] 미슐랭 채우기: 반영 ${resolved.length}개`,
  message: `미슐랭 채우기 끝: 반영 ${resolved.length}개` + (pending.length ? `, 아직 대기 ${pending.length}개 (하루 한도, 내일 이어서)` : '') +
           (j.exit_code ? `\n⚠️ exit ${j.exit_code}` : '') } }];"""

NOTE_B = ("## 미슐랭 채우기\nParse 하루 한도(100회)로 못 채운 식당을 하루 90회까지 채우고, 이미 적재된 영상은 DB에도 반영한다. "
          "호출 사이 12.5초 간격이라 최대 약 20분.\n\n영상 처리 워크플로우와 동시에 돌 수 없다(API가 409). 겹치면 시작되지 않고 다음날 다시 시도한다. "
          "반영된 게 있거나 대기가 남았을 때만 `결과 메일`이 간다(SMTP 자격 증명 선택 필요).")

B = {
    "name": "미슐랭 채우기 (매일)",
    "nodes": [
        node("매일 09시", "n8n-nodes-base.scheduleTrigger", {"rule": {"interval": [{"field": "days", "daysInterval": 1, "triggerAtHour": 9}]}}, [0, 0], 1.2),
        node("수동 실행", "n8n-nodes-base.manualTrigger", {}, [0, 160], 1),
        http("미슐랭 작업 시작", "POST", "/michelin", [260, 80], '{"max_calls": 90}'),
        if_bool("시작됐나?", "={{ $json.started }}", [520, 80]),
        wait("2분 대기", 120, [780, 0]),
        http("작업 상태", "GET", "/job", [1040, 0]),
        if_bool("아직 실행 중?", "={{ $json.running }}", [1300, 0]),
        code("결과 정리", MICH_JS, [1560, 100]),
        email("결과 메일", [1820, 100]),
        sticky("B", NOTE_B, [260, -260], 560, 220),
    ],
    "connections": conn(
        ("매일 09시", 0, "미슐랭 작업 시작"), ("수동 실행", 0, "미슐랭 작업 시작"), ("미슐랭 작업 시작", 0, "시작됐나?"),
        ("시작됐나?", 0, "2분 대기"), ("2분 대기", 0, "작업 상태"), ("작업 상태", 0, "아직 실행 중?"),
        ("아직 실행 중?", 0, "2분 대기"), ("아직 실행 중?", 1, "결과 정리"), ("결과 정리", 0, "결과 메일")),
    "settings": {"executionOrder": "v1", "timezone": "Asia/Seoul"},
}

if __name__ == "__main__":
    out = Path(__file__).resolve().parent / "workflows"
    out.mkdir(exist_ok=True)
    for fname, wf in (("new_video_pipeline.json", A), ("fill_michelin_daily.json", B)):
        names = {n["name"] for n in wf["nodes"]}
        for src, c in wf["connections"].items():  # 연결이 가리키는 노드가 모두 있는지 확인
            assert src in names, src
            assert all(t["node"] in names for outs in c["main"] for t in outs)
        (out / fname).write_text(json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")
        print("wrote", fname, f"({len(wf['nodes'])} nodes)")
