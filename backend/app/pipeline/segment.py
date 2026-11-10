"""Split a multi-page document into invoice-level segments (never send 1000 pages to an LLM at once)."""
from dataclasses import dataclass
from .extract_rules import find_invoice_number


@dataclass
class Segment:
    no: int
    page_start: int
    page_end: int
    text: str


def segment_pages(pages) -> list[Segment]:
    segs: list[Segment] = []
    cur_no = None
    for p in pages:
        num = find_invoice_number("\n".join(p.text.splitlines()[:15]))
        if not segs or (num and cur_no and num != cur_no):
            segs.append(Segment(len(segs), p.page_no, p.page_no, p.text))
            cur_no = num
        else:
            s = segs[-1]
            s.page_end = p.page_no
            s.text += "\n" + p.text
            cur_no = cur_no or num
    return segs
