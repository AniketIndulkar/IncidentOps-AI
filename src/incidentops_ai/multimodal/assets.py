"""The multimodal benchmark (AIENG-39): 14 synthetic assets and 28 questions, drawn by code.

    uv run python -m incidentops_ai.multimodal.assets      # rewrites evals/multimodal/

Drawing and expected answers live together so they cannot drift apart. Output is deterministic
(fixed seeds, a fixed PDF creation date), so regenerating gives byte-identical files.

Some answers are deliberately visible only as a picture: which line spikes, which bar is tallest,
which box carries a red cross, where an arrow points. Diagram relations use components a model cannot
guess from their names. Needs the dev dependencies (Pillow, fpdf2).
"""

import io
import json
import math
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

OUT_DIR = Path("evals/multimodal")
PDF_DATE = datetime(2026, 10, 17, tzinfo=UTC)

DARK, LIGHT, GREY, RED, GREEN = "#1e1e1e", "#e6e6e6", "#777777", "#d0312d", "#2e8b57"
SERIES = {"us-east-1": "#1f77b4", "eu-west-1": "#ff7f0e", "ap-south-1": "#2ca02c"}


@dataclass(frozen=True)
class QuestionSpec:
    question: str
    expected: tuple[str, ...]  # every term must appear in the answer; "a|b" allows either


@dataclass(frozen=True)
class AssetSpec:
    id: str
    kind: str  # screenshot | chart | diagram | pdf-text | pdf-scanned
    filename: str
    title: str
    draw: Callable[[], bytes]
    questions: tuple[QuestionSpec, ...]


def _font(size: int):
    from PIL import ImageFont

    return ImageFont.load_default(size=size)


def _png(image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _canvas(width: int, height: int, colour: str = "white"):
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (width, height), colour)
    return image, ImageDraw.Draw(image)


# ── Screenshots ───────────────────────────────────────────────────────────────────────────────


def terminal_log() -> bytes:
    image, d = _canvas(1100, 330, DARK)
    d.text(
        (16, 10),
        "chen@bastion: ~ $ kubectl logs deploy/notification-worker -n messaging --since=1h",
        fill="#9cdcfe",
        font=_font(18),
    )
    lines = [
        "2026-02-03 03:20:58 INFO  consumer heartbeat ok group=notification-workers",
        "2026-02-03 03:21:04 ERROR kafka.errors.NoBrokersAvailable: NoBrokersAvailable",
        "2026-02-03 03:21:09 WARN  bootstrap kafka-bootstrap:9093 unreachable (attempt 2)",
        "2026-02-03 03:21:19 ERROR kafka.errors.NoBrokersAvailable: NoBrokersAvailable",
        "2026-02-03 03:21:39 WARN  bootstrap kafka-bootstrap:9093 unreachable (attempt 3)",
        "2026-02-03 03:22:19 ERROR delivery paused: 0 brokers reachable",
    ]
    for i, line in enumerate(lines):
        colour = "#f48771" if "ERROR" in line else LIGHT
        d.text((16, 52 + i * 44), line, fill=colour, font=_font(20))
    return _png(image)


def kubectl_pods() -> bytes:
    image, d = _canvas(1000, 260, DARK)
    d.text(
        (16, 10),
        "$ kubectl get pods -n discovery -l app=search-indexer",
        fill="#9cdcfe",
        font=_font(18),
    )
    header = ("NAME", "READY", "STATUS", "RESTARTS", "AGE")
    rows = [
        ("search-indexer-qhxtv", "1/1", "Running", "0", "2d"),
        ("search-indexer-mbzrw", "0/1", "OOMKilled", "11", "14m"),
        ("search-indexer-pfjnd", "0/1", "CrashLoopBackOff", "6", "14m"),
    ]
    columns = (16, 330, 430, 680, 830)
    for row_index, row in enumerate((header, *rows)):
        for x, cell in zip(columns, row, strict=True):
            d.text((x, 56 + row_index * 46), cell, fill=LIGHT, font=_font(20))
    return _png(image)


def browser_502() -> bytes:
    image, d = _canvas(1000, 520, "white")
    d.rectangle((0, 0, 1000, 60), fill="#dee1e6")
    d.rounded_rectangle((90, 14, 900, 46), radius=14, fill="white")
    d.text((110, 19), "https://shop.example.com/checkout/payment", fill="#333333", font=_font(18))
    d.text((360, 170), "502 Bad Gateway", fill="black", font=_font(44))
    d.line((80, 260, 920, 260), fill="#cccccc", width=2)
    d.text((440, 280), "nginx/1.25.3", fill="black", font=_font(24))
    return _png(image)


def alert_card() -> bytes:
    image, d = _canvas(900, 330, "#f8f8f8")
    d.rectangle((0, 0, 10, 330), fill=RED)
    d.text((30, 18), "Alertmanager  APP  16:44", fill=GREY, font=_font(18))
    d.text((30, 54), "[FIRING:1] PaymentsChargeLatencyHigh", fill="black", font=_font(26))
    d.text(
        (30, 110),
        "service=payments-gateway  region=eu-west-1  acquirer=primary",
        fill="#222222",
        font=_font(20),
    )
    d.text((30, 160), "p99 /v1/charges = 9.1 s (threshold 3 s)", fill="#222222", font=_font(20))
    d.text(
        (30, 210),
        "Started 16:44 UTC   Runbook: rb-payments-latency",
        fill="#222222",
        font=_font(20),
    )
    return _png(image)


# ── Charts: the answer is a shape, not a string ────────────────────────────────────────────────


def _axes(d, left, top, right, bottom, y_max, y_step, y_label, x_labels):
    d.line((left, top, left, bottom), fill="black", width=2)
    d.line((left, bottom, right, bottom), fill="black", width=2)
    for value in range(0, y_max + 1, y_step):
        y = bottom - (bottom - top) * value / y_max
        d.line((left - 6, y, left, y), fill="black", width=2)
        d.text((left - 44, y - 10), str(value), fill="black", font=_font(16))
    d.text((left - 60, top - 36), y_label, fill="black", font=_font(16))
    for i, label in enumerate(x_labels):
        x = left + (right - left) * i / (len(x_labels) - 1)
        d.text((x - 22, bottom + 10), label, fill="black", font=_font(16))


def latency_chart() -> bytes:
    image, d = _canvas(1000, 560)
    left, top, right, bottom = 90, 70, 760, 480
    d.text((260, 16), "p99 latency of /v1/charges by region", fill="black", font=_font(22))
    _axes(
        d, left, top, right, bottom, 10, 2, "seconds", ["16:30", "16:45", "17:00", "17:15", "17:30"]
    )
    rng = random.Random(6)
    for region, colour in SERIES.items():
        points = []
        for step in range(61):
            base = {"us-east-1": 0.45, "eu-west-1": 0.4, "ap-south-1": 0.6}[region]
            value = base + rng.uniform(-0.08, 0.08)
            if region == "eu-west-1" and 14 <= step <= 36:
                value = 9.0 + rng.uniform(-0.2, 0.15)
            x = left + (right - left) * step / 60
            y = bottom - (bottom - top) * value / 10
            points.append((x, y))
        d.line(points, fill=colour, width=3)
    for i, (region, colour) in enumerate(SERIES.items()):
        y = 120 + i * 40
        d.rectangle((790, y, 812, y + 22), fill=colour)
        d.text((822, y), region, fill="black", font=_font(18))
    return _png(image)


def error_rate_bars() -> bytes:
    image, d = _canvas(1000, 560)
    left, top, right, bottom = 90, 70, 900, 470
    d.text((300, 16), "5xx error rate by service (last hour)", fill="black", font=_font(22))
    _axes(d, left, top, right, bottom, 10, 2, "percent", ["", "", "", "", ""])
    bars = [
        ("orders-api", 2.1),
        ("checkout-api", 8.7),
        ("payments-gateway", 3.4),
        ("session-service", 0.6),
    ]
    width = (right - left) / len(bars)
    for i, (name, value) in enumerate(bars):
        x0 = left + i * width + 30
        x1 = x0 + width - 60
        d.rectangle((x0, bottom - (bottom - top) * value / 10, x1, bottom), fill="#4c72b0")
        d.text((x0, bottom + 12), name, fill="black", font=_font(16))
    return _png(image)


def connection_states() -> bytes:
    image, d = _canvas(1000, 560)
    left, top, right, bottom = 100, 80, 880, 470
    d.text((230, 16), "orders-db-primary connections by state", fill="black", font=_font(22))
    _axes(d, left, top, right, bottom, 250, 50, "connections", ["", "", "", "", ""])
    limit_y = bottom - (bottom - top) * 200 / 250
    for x in range(left, right, 24):
        d.line((x, limit_y, x + 12, limit_y), fill=RED, width=2)
    d.text((right - 170, limit_y - 28), "max_connections 200", fill=RED, font=_font(16))
    bars = [("active", 40), ("idle", 25), ("idle in transaction", 135)]
    width = (right - left) / len(bars)
    for i, (name, value) in enumerate(bars):
        x0 = left + i * width + 40
        x1 = x0 + width - 80
        d.rectangle((x0, bottom - (bottom - top) * value / 250, x1, bottom), fill="#55a868")
        d.text((x0, bottom + 12), name, fill="black", font=_font(16))
    return _png(image)


# ── Diagrams: relations and visual marks ──────────────────────────────────────────────────────


def _box(d, x, y, label, *, w=200, h=64, outline="black", fill="white"):
    d.rounded_rectangle((x, y, x + w, y + h), radius=10, outline=outline, width=3, fill=fill)
    d.text((x + 14, y + 20), label, fill="black", font=_font(18))
    return (x, y, x + w, y + h)


def _arrow(d, start, end, colour="black"):
    d.line((start, end), fill=colour, width=3)
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    size = 14
    left = (end[0] - size * math.cos(angle - 0.4), end[1] - size * math.sin(angle - 0.4))
    right = (end[0] - size * math.cos(angle + 0.4), end[1] - size * math.sin(angle + 0.4))
    d.polygon((end, left, right), fill=colour)


def _right_mid(box):
    return (box[2], (box[1] + box[3]) / 2)


def _left_mid(box):
    return (box[0], (box[1] + box[3]) / 2)


def architecture() -> bytes:
    image, d = _canvas(1100, 620)
    d.text((380, 14), "Commerce request flow (Q4 2026)", fill="black", font=_font(22))
    checkout = _box(d, 40, 120, "checkout-api")
    billing = _box(d, 40, 400, "billing-service")
    payments = _box(d, 450, 60, "payments-gateway")
    promo = _box(d, 450, 220, "promo-engine")
    orders = _box(d, 450, 380, "orders-api")
    ledger = _box(d, 860, 160, "ledger-writer")
    orders_db = _box(d, 860, 440, "orders-db")
    _arrow(d, _right_mid(checkout), _left_mid(payments))
    _arrow(d, _right_mid(checkout), _left_mid(promo))
    _arrow(d, _right_mid(payments), _left_mid(ledger))
    _arrow(d, _right_mid(billing), (ledger[0], ledger[3] - 10))
    _arrow(d, _right_mid(orders), _left_mid(orders_db))
    return _png(image)


def failover() -> bytes:
    image, d = _canvas(1000, 520)
    d.text((330, 14), "orders database, 21 May 09:58", fill="black", font=_font(22))
    _box(d, 150, 160, "orders-db-primary", w=250, fill="#fde8e8", outline=RED)
    standby = _box(d, 600, 160, "orders-db-standby", w=250, fill="#e6f4ea", outline=GREEN)
    d.line((60, 160, 120, 224), fill=RED, width=8)  # a red cross beside the failed node
    d.line((120, 160, 60, 224), fill=RED, width=8)
    d.line((870, 196, 890, 216, 925, 166), fill=GREEN, width=8, joint="curve")  # a green tick
    api = _box(d, 380, 380, "orders-api", w=180)
    _arrow(d, (api[0] + 140, api[1]), ((standby[0] + standby[2]) / 2, standby[3]), colour=GREY)
    return _png(image)


def request_path() -> bytes:
    image, d = _canvas(1100, 460)
    d.text((380, 14), "web-dashboard request path", fill="black", font=_font(22))
    client = _box(d, 30, 180, "browser", w=150)
    cdn = _box(d, 300, 180, "CDN", w=150)
    bucket = _box(d, 580, 180, "object storage", w=200)
    proxy = _box(d, 300, 340, "internal-auth-proxy", w=230)
    admin = _box(d, 650, 340, "admin-tools", w=180)
    _arrow(d, _right_mid(client), _left_mid(cdn))
    _arrow(d, _right_mid(cdn), _left_mid(bucket))
    _arrow(d, (client[2] - 40, client[3]), _left_mid(proxy))
    _arrow(d, _right_mid(proxy), _left_mid(admin))
    return _png(image)


# ── PDFs ───────────────────────────────────────────────────────────────────────────────────────


def _pdf():
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_creation_date(PDF_DATE)
    pdf.set_producer("IncidentOps benchmark")
    pdf.set_auto_page_break(auto=True, margin=15)
    return pdf


def _pdf_table(pdf, header, rows, widths):
    pdf.set_font("Helvetica", "B", 11)
    for cell, width in zip(header, widths, strict=True):
        pdf.cell(width, 9, cell, border=1)
    pdf.ln()
    pdf.set_font("Helvetica", "", 11)
    for row in rows:
        for cell, width in zip(row, widths, strict=True):
            pdf.cell(width, 9, cell, border=1)
        pdf.ln()


def oncall_rota() -> bytes:
    pdf = _pdf()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 12, "Commerce Platform on-call rota, Q4 2026", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(
        0,
        6,
        "Hand-over happens on Monday at 10:00 UTC. The secondary covers the primary "
        "during leave and joins every SEV1.",
    )
    pdf.ln(4)
    rows = [
        ("43", "Amara Okafor", "Lukas Becker"),
        ("44", "Lukas Becker", "Priya Nair"),
        ("45", "Tom Hughes", "Amara Okafor"),
        ("46", "Amara Okafor", "Tom Hughes"),
    ]
    _pdf_table(pdf, ("Week", "Primary", "Secondary"), rows, (30, 70, 70))
    return bytes(pdf.output())


def sla_policy() -> bytes:
    pdf = _pdf()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 12, "Incident response targets", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(
        0,
        6,
        "These targets apply to customer-facing services. The clock starts at the "
        "first alert or customer report, whichever is earlier.",
    )
    pdf.ln(4)
    rows = [
        ("SEV1", "15 minutes", "every 15 minutes", "5 working days"),
        ("SEV2", "30 minutes", "every 30 minutes", "10 working days"),
        ("SEV3", "next business day", "daily", "not required"),
    ]
    _pdf_table(
        pdf, ("Severity", "First response", "Updates", "Postmortem due"), rows, (28, 45, 50, 50)
    )
    return bytes(pdf.output())


def _scanned_pdf(draw_page: Callable[[], object], seed: int) -> bytes:
    """An image-only PDF: the page is rendered, rotated a little and given noise, like a scan."""
    from PIL import Image

    page = draw_page().convert("L").rotate(1.2, resample=Image.BICUBIC, expand=False, fillcolor=255)
    rng = random.Random(seed)
    pixels = page.load()
    for _ in range(9000):
        x, y = rng.randrange(page.width), rng.randrange(page.height)
        pixels[x, y] = rng.choice((90, 140, 200))
    buffer = io.BytesIO()
    page.save(buffer, format="PNG")
    pdf = _pdf()
    pdf.add_page()
    pdf.image(io.BytesIO(buffer.getvalue()), x=5, y=5, w=200)
    return bytes(pdf.output())


def _table_page(title, header, rows, columns):
    image, d = _canvas(1240, 900)
    d.text((60, 50), title, fill="black", font=_font(34))
    for row_index, row in enumerate((header, *rows)):
        y = 160 + row_index * 70
        d.line((50, y - 12, 1150, y - 12), fill=GREY, width=2)
        for x, cell in zip(columns, row, strict=True):
            d.text((x, y), cell, fill="black", font=_font(28))
    return image


def capacity_plan() -> bytes:
    return _scanned_pdf(
        lambda: _table_page(
            "orders-api capacity plan (scanned)",
            ("Month", "Peak orders/s", "Pods"),
            [("October", "85", "12"), ("November", "120", "16"), ("December", "160", "22")],
            (70, 420, 820),
        ),
        seed=11,
    )


def maintenance_windows() -> bytes:
    return _scanned_pdf(
        lambda: _table_page(
            "Maintenance windows (scanned)",
            ("Service", "Window (UTC)", "Owner"),
            [
                ("orders-db", "Sun 02:00-04:00", "Database Reliability"),
                ("kafka", "Sat 01:00-02:00", "Messaging"),
                ("redis-sessions", "Tue 03:00-03:30", "Identity"),
            ],
            (70, 400, 820),
        ),
        seed=12,
    )


Q = QuestionSpec
ASSETS: tuple[AssetSpec, ...] = (
    AssetSpec(
        "s01",
        "screenshot",
        "s01-terminal-notification-worker.png",
        "notification-worker logs",
        terminal_log,
        (
            Q("Which exception does notification-worker log?", ("NoBrokersAvailable",)),
            Q("At what time does the first error line appear?", ("03:21:04",)),
        ),
    ),
    AssetSpec(
        "s02",
        "screenshot",
        "s02-kubectl-search-indexer.png",
        "search-indexer pods",
        kubectl_pods,
        (
            Q("How many restarts does search-indexer-mbzrw show?", ("11",)),
            Q("Which search-indexer pod is in CrashLoopBackOff?", ("pfjnd",)),
        ),
    ),
    AssetSpec(
        "s03",
        "screenshot",
        "s03-browser-502.png",
        "checkout error page",
        browser_502,
        (
            Q("What HTTP status does the page show?", ("502",)),
            Q("Which server software and version produced the page?", ("nginx", "1.25.3")),
        ),
    ),
    AssetSpec(
        "s04",
        "screenshot",
        "s04-alert-payments.png",
        "payments alert",
        alert_card,
        (
            Q("Which region does the alert name?", ("eu-west-1",)),
            Q("What p99 latency does the alert report?", ("9.1",)),
        ),
    ),
    AssetSpec(
        "c01",
        "chart",
        "c01-latency-by-region.png",
        "latency by region",
        latency_chart,
        (
            Q("Which region's latency spikes in this chart?", ("eu-west-1",)),
            Q("Roughly what value, in seconds, does the spiking line reach?", ("9|nine",)),
        ),
    ),
    AssetSpec(
        "c02",
        "chart",
        "c02-error-rate-by-service.png",
        "error rate by service",
        error_rate_bars,
        (
            Q("Which service has the highest error rate?", ("checkout-api",)),
            Q("Which service has the lowest error rate?", ("session-service",)),
        ),
    ),
    AssetSpec(
        "c03",
        "chart",
        "c03-connections-by-state.png",
        "connections by state",
        connection_states,
        (
            Q("Which connection state has the most connections?", ("idle in transaction",)),
            Q("Which state has the fewest connections?", ("idle",)),
        ),
    ),
    AssetSpec(
        "d01",
        "diagram",
        "d01-commerce-architecture.png",
        "commerce architecture",
        architecture,
        (
            Q(
                "Which two services send arrows into ledger-writer?",
                ("billing-service", "payments-gateway"),
            ),
            Q("Besides payments-gateway, what does checkout-api call?", ("promo-engine",)),
        ),
    ),
    AssetSpec(
        "d02",
        "diagram",
        "d02-orders-db-failover.png",
        "orders database failover",
        failover,
        (
            Q("Which database node is marked as failed?", ("primary",)),
            Q("Which database node has the green check mark?", ("standby",)),
        ),
    ),
    AssetSpec(
        "d03",
        "diagram",
        "d03-request-path.png",
        "web-dashboard request path",
        request_path,
        (
            Q("What sits between the browser and the object storage?", ("CDN",)),
            Q("What does internal-auth-proxy forward requests to?", ("admin-tools",)),
        ),
    ),
    AssetSpec(
        "p01",
        "pdf-text",
        "p01-oncall-rota.pdf",
        "on-call rota",
        oncall_rota,
        (
            Q("Who is the primary on-call engineer in week 45?", ("Tom Hughes",)),
            Q("Who is secondary in week 44?", ("Priya Nair",)),
        ),
    ),
    AssetSpec(
        "p02",
        "pdf-text",
        "p02-sla-policy.pdf",
        "incident response targets",
        sla_policy,
        (
            Q("What is the first-response target for a SEV2?", ("30 minutes|30 min",)),
            Q("When is a SEV1 postmortem due?", ("5|five", "working days")),
        ),
    ),
    AssetSpec(
        "p03",
        "pdf-scanned",
        "p03-capacity-plan-scanned.pdf",
        "capacity plan",
        capacity_plan,
        (
            Q("What is the planned peak orders per second for November?", ("120",)),
            Q("How many pods are planned for December?", ("22",)),
        ),
    ),
    AssetSpec(
        "p04",
        "pdf-scanned",
        "p04-maintenance-windows-scanned.pdf",
        "maintenance windows",
        maintenance_windows,
        (
            Q("When is the orders-db maintenance window?", ("Sun", "02:00")),
            Q("Who owns the kafka maintenance window?", ("Messaging",)),
        ),
    ),
)


def write_benchmark(out_dir: Path = OUT_DIR) -> Path:
    assets_dir = out_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for asset in ASSETS:
        (assets_dir / asset.filename).write_bytes(asset.draw())
        record = {
            "id": asset.id,
            "kind": asset.kind,
            "file": f"assets/{asset.filename}",
            "title": asset.title,
            "questions": [
                {"id": f"{asset.id}-q{i}", "question": q.question, "expected": list(q.expected)}
                for i, q in enumerate(asset.questions, start=1)
            ],
        }
        lines.append(json.dumps(record))
    cases = out_dir / "cases.jsonl"
    cases.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return cases


if __name__ == "__main__":
    print(f"wrote {write_benchmark()}")
