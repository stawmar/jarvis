#!/usr/bin/env python3
"""JARVIS: read-only pre-import of biological-material cases (BES / ZLO).

Reads DOCX and PDF files in Po_OCR without modifying originals.
Results are *proposals*, not verified laboratory findings. No FID/Excel used.

Requirements: python-docx (already used by document_scanner.py).
For PDF-only cases: system pdftotext (poppler-utils) OR Python pypdf.
"""

import json
import re
import shutil
import subprocess
import unicodedata
from collections import defaultdict
from pathlib import Path

SOURCE = Path(
    "/srv/data/nextcloud/data/marek/files/Raporty/JARVIS_TEST/Po_OCR"
)
DEST = Path("cases/biological_scan.json")

SAMPLE_RE = re.compile(
    r"(?<![A-Z0-9])(?:LU|WR|ST)\s*[-–—]?\s*\d{2}[\s.\-_–—]*\d{4,6}(?!\d)",
    re.IGNORECASE,
)
DATE_SAMPLE_RE = re.compile(
    r"(?<!\d)\d{2}[/-]\d{2}[/-]\d{4}\s*\[\s*[A-Z]{2,8}\s*\]",
    re.IGNORECASE,
)
DS_RE = re.compile(
    r"(?<!\d)(\d{4})\s*-\s*([\dOo])\s*\.?\s*[Dd][Ss]\s*\.?\s*([\dlI]{2,6})\s*[.,]\s*(20\d{2})\b"
)
BES_OTHER_RE = re.compile(r"\bWR\s*\.\s*\d+\s*\.\s*RSD\s*\.\s*\d+\s*\.\s*20\d{2}\b", re.I)
MATERIAL_PATTERNS = [
    ("krew", r"\bkr(?:e|w)w\b|\bkrwi\b|\bkrwia\b"),
    ("mocz", r"\bmocz(?:u|em|)?\b"),
    ("ciałko szkliste", r"\bcia[łl]k[oa]\s+szklist\w*"),
    ("mięsień", r"\bmi[eę]śni\w*|\bmi[eę]sni\w*"),
]


def read_docx(path):
    from docx import Document

    document = Document(path)
    paragraphs = [p.text.strip() for p in document.paragraphs if p.text.strip()]
    table_texts = [
        " | ".join(cell.text for cell in row.cells)
        for table in document.tables
        for row in table.rows
    ]
    return "\n".join(paragraphs + table_texts)


def read_pdf_pages(path):
    if shutil.which("pdftotext"):
        p = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            capture_output=True, text=True, check=True,
        )
        return [p for p in p.stdout.split("\f") if p.strip()]
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "Brak odczytu PDF: zainstaluj poppler-utils (pdftotext) lub pypdf"
        ) from exc
    return [page.extract_text() or "" for page in PdfReader(str(path)).pages]


def asciify(text):
    return "".join(
        c for c in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(c)
    ).lower()


def unique(items):
    return list(dict.fromkeys(x for x in items if x))


def norm_sample(s):
    s = re.sub(r"\s+", "", s.upper()).replace("–", "-").replace("—", "-")
    if "/" in s and "[" in s:
        return s
    s = re.sub(r"^([A-Z]{2})[._-]?(\d{2})[._-]+(\d{4,6})[._-]*$", r"\1\2-\3", s)
    return s


def sample_ids(text):
    all_ids = []
    for pattern in (SAMPLE_RE, DATE_SAMPLE_RE):
        all_ids.extend((m.start(), norm_sample(m.group())) for m in pattern.finditer(text))
    return unique(val for _, val in sorted(all_ids))


def case_ids(text):
    found = []
    for m in DS_RE.finditer(text):
        prefix = "0" if m.group(2).upper() == "O" else m.group(2)
        seq = m.group(3).replace("l", "1").replace("I", "1")
        found.append(f"{m.group(1)}-{prefix}.Ds.{seq}.{m.group(4)}")
    found.extend(re.sub(r"\s+", "", m.group()) for m in BES_OTHER_RE.finditer(text))
    return unique(found)


def material_of(cover):
    lowered = asciify(cover)
    # Only inspect the first-page material field / cover, not all legal context.
    if "material do badan" in lowered or "materialfor examination" in lowered:
        idx = lowered.find("material do badan")
        if idx < 0:
            idx = lowered.find("materialfor examination")
        excerpt = lowered[idx: idx + 270]
    else:
        idx = lowered.find("material dowodowy w postaci")
        excerpt = lowered[idx: idx + 370] if idx >= 0 else lowered[:600]
    matches = [label for label, pattern in MATERIAL_PATTERNS if re.search(pattern, excerpt)]
    if not matches:
        # OCR can spell KREW as K R E W; ZLO's explicit form value is still authoritative.
        prefix = lowered[:2800]
        if re.search(r"(?<![a-z])k\s*r\s*e\s*w(?![a-z])", prefix):
            matches.append("krew")
    return matches or ["do ustalenia"]


def analysis_of(cover, docx_text, filename, source):
    # ZLO form's section 10 is the strongest procedure-scope source.
    def form_scope(t):
        lo = asciify(t)
        m = re.search(r"(?:ustalenia do zlecenia|comments on equipment)", lo)
        if not m:
            return ""
        piece = lo[m.start():m.start() + 650]
        # Stop before unrelated statements; OCR may garble that boundary.
        end = re.search(r"oswiadczenie|declaration of contractee", piece)
        return piece[:end.start()] if end else piece

    scope = form_scope(docx_text) or form_scope(cover)
    if not scope and source == "BES":
        scope = asciify(cover)
    # If PDF OCR broke the scope, combine with alternate DOCX if available.
    alternate = form_scope(cover)
    combined = scope + " " + alternate
    alc = bool(re.search(r"alkohol|etanol", combined))
    narc = bool(re.search(r"odurza|odurzaj|narkot|psychotrop", combined))

    panel = None
    panel_source = None
    if narc:
        if source == "BES":
            panel = "narko-full"
            panel_source = "reguła BES"
        elif re.search(r"narko[ _-]?full|pe[lł]ny\s+panel|peln[ya]\s+panel|peiny\s+panel|pe[lł]ny\s+zakres|zakres\s+rozszerzon", combined):
            panel = "narko-full"
            panel_source = "treść dokumentu"
        elif re.search(r"narko[ _-]?small|skrocon[ya]\s+panel|panel\s+podstawow", combined):
            panel = "narko-small"
            panel_source = "treść dokumentu"
        else:
            hint = asciify(filename)
            if "narko full" in hint or "narko-full" in hint:
                panel, panel_source = "narko-full", "nazwa pliku — do weryfikacji"
            elif "narko small" in hint or "narko-small" in hint:
                panel, panel_source = "narko-small", "nazwa pliku — do weryfikacji"
            else:
                panel = "nieustalony"
                panel_source = "brak jednoznacznego oznaczenia"

    return {"alcohol": alc, "narcotics": narc,
            "narcoticsPanel": panel, "panelSource": panel_source}


def person_from_cover(cover, sample_list, source):
    candidates = []
    for s in sample_list:
        # Find person after sample id; avoid reaching into unrelated next sections.
        patterns = [re.escape(s)]
        if re.match(r"^(LU|WR|ST)\d{2}-", s):
            p = re.match(r"^([A-Z]{2})(\d{2})-(\d+)$", s)
            patterns = [rf"{p.group(1)}\s*[._–—-]?\s*{p.group(2)}[\s._–—-]*{p.group(3)}"]
        for pattern in patterns:
            m = re.search(pattern, cover, re.I)
            if not m:
                continue
            nearby = cover[m.end():m.end()+95]
            m_name = re.search(r"\[\s*([A-ZĄĆĘŁŃÓŚŹŻa-ząćęłńóśźż]+(?:\s+[A-ZĄĆĘŁŃÓŚŹŻa-ząćęłńóśźż]+){1,4})\s*\]", nearby)
            if not m_name and source == "BES":
                m_name = re.search(r"\s+([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+\s+[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)\s*\(", nearby)
            if m_name:
                candidates.append({"sample": s, "name": m_name.group(1).strip()})
                break
    return candidates


def unique_persons(items):
    seen, result = set(), []
    for item in items:
        key = (item["sample"], item["name"])
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def scan_file(stem, docx_path, pdf_path):
    docx_text = read_docx(docx_path) if docx_path else ""
    pdf_pages = read_pdf_pages(pdf_path) if pdf_path else []
    if pdf_pages:
        cover = pdf_pages[0]
        post = "\n".join(pdf_pages[1:])
    else:
        # DOCX-only fallback: separate at the formal decision title where possible.
        m = re.search(r"\bPOSTANOWIENIE\b", docx_text)
        if m and m.start() > 500:
            cover, post = docx_text[:m.start()], docx_text[m.start():]
        else:
            cover, post = docx_text[:1800], docx_text[1800:]

    source = "ZLO" if stem.upper().startswith("ZLO") else "BES" if stem.upper().startswith("BES") else "UNKNOWN"
    material = material_of(cover + "\n" + docx_text[:800])
    if material == ["do ustalenia"]:
        # Last fallback for difficult OCR of the first page.
        other = material_of(docx_text[:1600]) if docx_text else material
        material = other
    if material == ["do ustalenia"]:
        return None  # Not clearly a biological case; leave existing solid scanner alone.

    # PDF page boundaries reliably separate cover from formal order, while
    # DOCX often reads sample IDs more accurately than OCR embedded in PDF.
    docx_cover, docx_post = "", ""
    if docx_text:
        heading = re.search(r"\bPOSTANOWIENIE\b", docx_text[900:], re.I)
        cutoff = heading.start() + 900 if heading else len(docx_text)
        docx_cover, docx_post = docx_text[:cutoff], docx_text[cutoff:]
    form_ids = unique(sample_ids(docx_cover) + sample_ids(cover))
    post_ids = unique(sample_ids(post) + sample_ids(docx_post))
    document_ids = sample_ids(docx_text) if docx_text else []
    if not form_ids:
        # DOCX sometimes preserves more text than scanned PDF.
        form_ids = [x for x in document_ids if x not in post_ids]
    if not post_ids and not pdf_pages:
        post_ids = [x for x in document_ids if x not in form_ids]
    all_ids = unique(form_ids + post_ids)

    cover_cases = case_ids(cover)
    post_cases = case_ids(post)
    docx_cases = case_ids(docx_text)
    if source == "BES":
        number = next(iter(cover_cases or docx_cases or post_cases), None)
    else:
        number = next(iter(post_cases or docx_cases or cover_cases), None)

    scope = analysis_of(cover, docx_text, stem, source)
    warnings = []
    only_post = sorted(set(post_ids) - set(form_ids)) if form_ids else []
    if only_post:
        warnings.append("Postanowienie zawiera dodatkowe/inne numery próbek: " + ", ".join(only_post))
    if not form_ids:
        warnings.append("Nie odczytano numeru próbki z formularza/pisma przewodniego")
    if source == "ZLO" and not number:
        warnings.append("Brak typowej sygnatury Ds — użyj identyfikatora postanowienia i numeru próbki")
    if scope["narcotics"] and scope["narcoticsPanel"] == "nieustalony":
        warnings.append("Nie ustalono wariantu procedury narkotykowej")
    if not scope["alcohol"] and not scope["narcotics"]:
        warnings.append("Nie odczytano jednoznacznie zakresu analiz")
    if source == "BES" and cover_cases and post_cases and cover_cases[0] != post_cases[0]:
        warnings.append("Rozbieżne sygnatury na piśmie BES i postanowieniu")
    if scope["panelSource"] == "nazwa pliku — do weryfikacji":
        warnings.append("Wariant narko wywnioskowany tylko z nazwy pliku — sprawdź oryginał")

    from_docx = person_from_cover(docx_cover, form_ids, source)
    from_pdf = person_from_cover(cover, form_ids, source)
    names = []
    for sid in form_ids:
        choice = next((item for item in from_docx if item["sample"] == sid), None)
        choice = choice or next((item for item in from_pdf if item["sample"] == sid), None)
        if choice:
            names.append(choice)
        alternatives = unique([item["name"] for item in from_pdf + from_docx if item["sample"] == sid])
        if len(alternatives) > 1:
            warnings.append(f"Rozbieżne odczyty nazwiska/imienia w OCR dla próbki {sid} — sprawdź skan")

    return {
        "document": stem,
        "source": source,
        "caseNumber": number,
        "coverCaseNumbers": cover_cases,
        "postCaseNumbers": post_cases,
        "material": material,
        "sampleIdentifiers": form_ids,  # Only cover-approved *candidates*, not every mention
        "postSampleIdentifiers": post_ids,
        "personCandidates": names,
        "analysis": scope,
        "usesFID": False,
        "usesExcel": False,
        "photographsAssigned": [],
        "sourceFiles": {"docx": docx_path.name if docx_path else None,
                        "pdf": pdf_path.name if pdf_path else None},
        "warnings": warnings,
        "approved": False,
    }


def main():
    if not SOURCE.is_dir():
        raise SystemExit(f"Brak katalogu: {SOURCE}")
    groups = defaultdict(dict)
    for p in SOURCE.iterdir():
        if p.is_file() and p.suffix.lower() in (".pdf", ".docx"):
            groups[p.stem][p.suffix.lower()] = p

    data, errors, ignored = [], [], []
    for stem, exts in sorted(groups.items()):
        try:
            result = scan_file(stem, exts.get(".docx"), exts.get(".pdf"))
            if result:
                data.append(result)
                num = result["caseNumber"] or "bez sygnatury Ds"
                scope = result["analysis"]
                kind = "+".join(k for k, enabled in (("alko", scope["alcohol"]), ("narko", scope["narcotics"])) if enabled) or "nieznany"
                print(f"{stem} | {result['source']} | {num} | {len(result['sampleIdentifiers'])} próbek | {kind} | {len(result['warnings'])} uwag")
                for w in result["warnings"]:
                    print("  !", w)
            else:
                ignored.append(stem)
        except Exception as e:
            errors.append({"document": stem, "message": str(e)})
            print(f"BŁĄD: {stem}: {e}")

    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_text(json.dumps({"cases": data, "errors": errors, "ignored": ignored}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSprawy biologiczne: {len(data)}, inne/nieustalone: {len(ignored)}, błędy: {len(errors)}")
    print(f"Zapisano: {DEST}")
    print("To tylko robocze propozycje — niczego nie wprowadzono do kreatora.")


if __name__ == "__main__":
    main()
