#!/usr/bin/env python3
"""JARVIS: konserwatywne rozpoznawanie dowodów rzeczowych w DOCX po OCR.

Zgodność z case_importer.py:
- wynik to LISTA rekordów z filename/status/data/ocrText/approved;
- data.evidence to lista słowników {number, description};
- pola warnings i reviewCandidates są dodatkową diagnostyką.

Bezpieczeństwo danych:
- nie wyliczamy dowodów z nazwy pliku ani ilości opakowań;
- nie przekształcamy numerów próbek biologicznych w dowody rzeczowe;
- nie uznajemy numeracji formularza, pytań czy adresów za dowody;
- niejednoznaczne pozycje pozostają w reviewCandidates.
"""

import argparse
import json
import re
import unicodedata
from collections import OrderedDict
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

SOURCE_DIR = Path(
    "/srv/data/nextcloud/data/marek/files/Raporty/JARVIS_TEST/Po_OCR"
)
OUTPUT_FILE = Path("document_scan.json")

# Sygnatury zachowujemy tak, jak zapisano je w dokumencie.
CASE_NUMBER = re.compile(r"\b\d{4}-\d+\.Ds\.\d+\.\d{4}(?:\.[A-Za-z])?\b", re.I)
INTERNAL_NUMBER = re.compile(r"\b\d{4}/CHR/\d{2}\b", re.I)

# Dokument prawdopodobnie przechodzi tu z części opisowej do postanowienia.
# Numerowane pytania w postanowieniu nie mogą być numerami dowodów.
ORDER_HEADING = re.compile(r"^\s*(?:P?OSTANOWIENIE|POSTANOWIENIE\s+O\s+DOPUSZCZENIU)\b", re.I)

# Przykłady: 'ślad/przedmiot 1 - susz', 'ślad/przedmiot nr 2', 'dowód rzeczowy nr 1.1A'.
EVIDENCE_NUMBER = r"\d{1,3}(?:[.,]\d{1,3})*(?:[A-Za-z]\d*)?"
EXPLICIT_EVIDENCE = re.compile(
    rf"(?<![\w/])(?:ślad\s*/\s*przedmiot|slad\s*/\s*przedmiot|"
    rf"ślad|slad|przedmiot|dow[oó]d\s+rzeczowy)"
    rf"\s*(?:nr\.?|numer|o\s+numerze)?\s*[:#-]?\s*"
    rf"(?P<number>{EVIDENCE_NUMBER})(?=$|[\s,;:()\]–—-])",
    re.I,
)

# Dla NIEJAWNYCH etykiet wymagamy aktywnej sekcji dowodów oraz opisu.
# Dopuszczamy: '1, susz', '2 - proszek', '1A, koperta', '1.1A – susz'.
NUMBERED_ITEM = re.compile(
    rf"^\s*(?:[-•*]\s*)?(?P<number>{EVIDENCE_NUMBER})"
    rf"\s*(?P<delimiter>[).,:;–—-]|\|)\s*(?P<description>.+?)\s*$",
    re.I,
)

# Nagłówki świadczące o wykazie dowodów. Nie wpisujemy tutaj samodzielnie
# 'materiał do badań', bo jest to również punkt 3 formularza ZLO.
PHYSICAL_HEADINGS = (
    "material dowodowy", "material rzeczowy", "dowody rzeczowe",
    "wykaz dowodow", "wykaz sladow", "wykaz przedmiotow",
    "slady i przedmioty", "przedmioty przekazane do badan",
    "opis materialu dowodowego", "wykaz zabezpieczonych rzeczy",
    "spis i opis rzeczy", "przekazany do badan material dowodowy",
)
END_HEADINGS = (
    "wyniki badan", "wyniki analiz", "metodyka badan", "metody badan",
    "przebieg badan", "wnioski", "pytania do bieglego", "opinia",
    "postanowienie", "oswiadczenie", "cel ekspertyzy", "zakres opinii",
)

# Charakterystyczny opis przedmiotu. Samo 'substancje' lub 'dowody' nie
# wystarcza: te słowa bardzo często występują także w pytaniach prawnych.
PHYSICAL_DESCRIPTION = re.compile(
    r"\b(?:susz\w*|roslin\w*|konopi\w*|krzak\w*|topow\w*|top[oó]w\w*|"
    r"marihuan\w*|haszysz\w*|amfetamin\w*|metamfetamin\w*|mdma|"
    r"prosz\w*|sproszkow\w*|skrystalizowan\w*|krysztal\w*|"
    r"tablet\w*|prob[oó]w\w*|worecz\w*|zawiniat\w*|kopert\w*|opakowan\w*|"
    r"pude[lł]\w*|torb\w*|fiolk\w*|bryl\w*|"
    r"substancj\w*\s+(?:koloru|barwy|proszkow\w*|skrystalizowan\w*))\b",
    re.I,
)

# Takie frazy nie są opisem dowodu, nawet jeśli mają poprzedzający je numer.
FORM_FIELDS = re.compile(
    r"\b(?:cena\s+netto|cena\s+brutto|kwota\s+podatku|termin\s+platnosci|"
    r"termin\s+wykonania|postepowanie\s+z\s+probka|ustalenia\s+do\s+zlecenia|"
    r"dni\s+od\s+daty\s+wystawienia|postanowienie|"
    r"wydzial\s+gospodarczy|krajowego\s+rejestru\s+sadowego)\b",
    re.I,
)

# Potencjalnie istotne opakowanie bez przypisania numeru dowodu.
PACKAGE_HINT = re.compile(
    r"\b(?:k\.?\s*bezp\.?|kopert\w*|worecz\w*|zawiniat\w*|"
    r"nr\s+[a-z]{2,5}[- ]?\d{5,})\b",
    re.I,
)
PHYSICAL_FILENAME_HINT = re.compile(
    r"(?:^|[^a-z0-9])(?:\d+\s*x\s*)?(?:susz|amfa|dop|hasz|mdma)(?=$|[^a-z])",
    re.I,
)


def normalize(text):
    """Upraszcza znaki tylko do porównań; nie zmienia oryginalnego OCR."""
    text = str(text).replace("ł", "l").replace("Ł", "L")
    text = "".join(
        char for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )
    return text.lower()


def clean(text):
    """Usuwa zakłócenia w białych znakach, bez ingerencji w treść."""
    text = str(text).replace("\xa0", " ").replace("\u200b", " ")
    return re.sub(r"\s+", " ", text).strip()


def read_docx(path):
    """Czyta akapity i tabele zgodnie z rzeczywistą kolejnością w DOCX."""
    doc = Document(path)
    lines = []

    for element in doc.element.body.iterchildren():
        if element.tag == qn("w:p"):
            line = clean(Paragraph(element, doc).text)
            if line:
                lines.append(line)
        elif element.tag == qn("w:tbl"):
            table = Table(element, doc)
            for row in table.rows:
                cells = [clean(cell.text) for cell in row.cells]
                if any(cells):
                    lines.append(" | ".join(cells))

    return "\n".join(lines)


def detect_source(text, filename):
    """Identyfikuje BES/ZLO, dopuszczając wskazówkę z nazwy pliku."""
    header = normalize(text[:2500])
    if "biuro ekspertyz sadowych" in header:
        return "BES"
    if "zespol lekarzy opiniujacych" in header:
        return "ZLO"
    if filename.upper().startswith("BES"):
        return "BES"
    if filename.upper().startswith("ZLO"):
        return "ZLO"
    return None


def split_before_order(text):
    """Zatrzymuje analizę dowodów przed pytaniami postanowienia."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if index > 0 and ORDER_HEADING.match(clean(line)):
            return lines[:index]
    return lines


def evidence_heading(line, source):
    """Nawet numerowany formularz ZLO nie jest wykazem dowodów rzeczowych."""
    flat = normalize(line)
    if any(term in flat for term in PHYSICAL_HEADINGS):
        return True
    # W starszych pismach BES krótszy nagłówek też bywa wykazem przedmiotów.
    if source == "BES" and "material do badan" in flat:
        return True
    return False


def end_heading(line):
    flat = normalize(line).lstrip(" -:0123456789.")
    return any(flat.startswith(term) for term in END_HEADINGS)


def physical_description(text):
    """Akceptuje konkretne przedmioty, nie formalne pola formularza."""
    flat = normalize(text)
    if FORM_FIELDS.search(flat):
        return False
    return bool(PHYSICAL_DESCRIPTION.search(flat))


def extract_evidence(text, source=None):
    """Zwraca dowody, ostrzeżenia i kandydatów wymagających sprawdzenia.

    Wpis do evidence wymaga jawnego numeru dowodu albo pozycji w wykazie
    i konkretnego opisu przedmiotu. Pozostałe ślady OCR nie są tracone.
    """
    lines = split_before_order(text)
    found = OrderedDict()
    warnings = []
    candidates = []
    in_section = False
    pending = None  # (numer, numer linii) dla opisu przeniesionego niżej

    def candidate(line_no, value, reason):
        # Deduplikacja niejednoznacznych fragmentów bez tworzenia dowodów.
        line_text = clean(value)[:320]
        if line_text and not any(
            c["line"] == line_no and c["text"] == line_text for c in candidates
        ):
            candidates.append({"line": line_no, "text": line_text, "reason": reason})

    def add(number, description, line_no, full_line):
        number = clean(number)
        description = clean(description).strip(" ,;:|–—-")

        if not physical_description(description):
            # Słabe dopasowania typu '21: 040 Świdnik' lub '6: los. wy'
            # nie są dowodami ani kandydatami — pochodzą zwykle z OCR adresów.
            # Mocne etykiety bez opisu obsługujemy osobno jako pending.
            return False

        current = found.get(number)
        if current:
            if normalize(current["description"]) != normalize(description):
                warnings.append(f"Powtórzony numer dowodu {number} z innym opisem — sprawdź OCR")
                candidate(line_no, full_line, "Powtórzony numer o innym opisie")
            return True

        found[number] = {"number": number, "description": description[:1500]}
        return True

    last_number = None  # Ostatni pewny dowód, do uzupełniania opisu wielowierszowego.

    for line_no, raw_line in enumerate(lines, start=1):
        line = clean(raw_line)
        if not line:
            continue

        if end_heading(line):
            in_section = False
            pending = None
            last_number = None
            continue

        # Nagłówek może sam zawierać pozycję, np. 'Materiał dowodowy: 1 – susz'.
        if evidence_heading(line, source):
            in_section = True
            last_number = None
            if ":" in line:
                line = line.split(":", 1)[1].strip()
            else:
                continue

        # Tylko opis bezpośrednio pod samotnym, jawnym numerem.
        if pending:
            number, previous_line = pending
            pending = None
            if line_no == previous_line + 1 and physical_description(line):
                add(number, line, line_no, f"{number}: {line}")
                continue
            candidate(previous_line, number, "Numer dowodu bez jednoznacznego opisu")

        # 'ślad/przedmiot [nr] 1 ...' — mocne oznaczenie, także poza sekcją.
        explicit_matches = list(EXPLICIT_EVIDENCE.finditer(line))
        if explicit_matches:
            for index, match in enumerate(explicit_matches):
                end = (
                    explicit_matches[index + 1].start()
                    if index + 1 < len(explicit_matches) else len(line)
                )
                description = line[match.end():end].strip(" -–—:;,|")
                number = match.group("number")
                if not description:
                    pending = (number, line_no)
                elif add(number, description, line_no, line):
                    last_number = number
                else:
                    candidate(line_no, line, "Jawne oznaczenie dowodu, ale opis nieczytelny")
            continue

        # Samodzielne numerowane pozycje wolno czytać TYLKO wewnątrz sekcji.
        if in_section:
            match = NUMBERED_ITEM.match(line)
            if match:
                number = match.group("number")
                description = match.group("description")
                if add(number, description, line_no, line):
                    last_number = number
                else:
                    last_number = None
                continue

            # Samotne '12.' w sekcji to kandydat, nie potwierdzony dowód.
            if re.fullmatch(
                r"\s*(?:[-•*]\s*)?\d{1,3}(?:[.,]\d{1,3})*[A-Za-z]?\s*[).,:;–—-]\s*",
                line,
            ):
                candidate(line_no, line, "Numerowana pozycja bez opisu")
                last_number = None
                continue

            # Wybrane frazy rozpoczynają opis rozpoczęty w poprzednim wierszu.
            continuation = re.match(
                r"^(?:zawarto[śs]ci[aą]|ro[śs]linnego|koloru|barwy|o\s+wadze|"
                r"o\s+masie|z\s+zawarto[śs]ci[aą])\b", line, re.I
            )
            if last_number and continuation and last_number in found:
                original = found[last_number]["description"]
                found[last_number]["description"] = (original + " " + line)[:1500]
                continue

        # Koperta/woreczek z OCR bez czytelnego numeru dowodu: do weryfikacji.
        flat = normalize(line)
        if PACKAGE_HINT.search(flat) and (in_section or "k. bezp" in flat):
            candidate(line_no, line, "Możliwy przedmiot bez numeru dowodu")
        elif in_section and physical_description(line):
            candidate(line_no, line, "Opis przedmiotu bez czytelnego numeru")
        elif source == "ZLO" and re.search(
            r"\bmaterial\s+do\s+badan\b", flat
        ) and re.search(r"\b(?:susz|haszysz|proszek|tabletki|konopie)\b", flat):
            # W ZLO pole 'Materiał do badań: SUSZ' potwierdza rodzaj
            # materiału, ale nie nadaje mu numeru dowodu rzeczowego.
            candidate(line_no, line, "Materiał rzeczowy w formularzu ZLO bez numeru dowodu")

    if pending:
        candidate(pending[1], pending[0], "Numer dowodu bez opisu")

    # Nie tworzymy fałszywych błędów w próbkach krwi; kandydaci i tak są
    # widoczni w JSON, gdy w dokumencie jest prawdziwy opis przedmiotu.
    if candidates:
        warnings.append("Niejednoznaczne fragmenty OCR — sprawdź reviewCandidates")

    return list(found.values()), list(dict.fromkeys(warnings)), candidates


def extract_data(text, filename=""):
    """Publiczny kontrakt pozostaje zgodny z dotychczasowym importerem."""
    source = detect_source(text, filename)
    case_match = CASE_NUMBER.search(text)
    internal_match = INTERNAL_NUMBER.search(text)
    evidence, _, _ = extract_evidence(text, source)

    return {
        "source": source,
        "caseNumber": case_match.group(0) if case_match else None,
        "internalNumber": internal_match.group(0) if internal_match else None,
        "evidence": evidence,
    }


def main():
    parser = argparse.ArgumentParser(description="JARVIS: skan dokumentów OCR")
    parser.add_argument("--source", type=Path, default=SOURCE_DIR)
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if not args.source.is_dir():
        parser.error(f"Nie istnieje katalog: {args.source}")

    records = []
    for path in sorted(args.source.glob("*.docx")):
        try:
            text = read_docx(path)
            data = extract_data(text, path.name)
            evidence, warnings, candidates = extract_evidence(text, data["source"])

            # Wzmianka z nazwy pliku tylko ostrzega, nigdy nie dopisuje dowodów.
            if not evidence and PHYSICAL_FILENAME_HINT.search(normalize(path.stem)):
                warnings.append("Nazwa wskazuje na materiał rzeczowy, lecz brak pewnych dowodów")

            records.append({
                "filename": path.name,
                "status": "OK",  # Status wykonania OCR, a nie zatwierdzenie zawartości.
                "data": data,
                "ocrText": text,
                "approved": False,
                "warnings": warnings,
                "reviewCandidates": candidates,
            })
            print(
                f"{path.name} | sprawa: {data['caseNumber']} | "
                f"wew: {data['internalNumber']} | dowody: {len(evidence)} | "
                f"ostrzeżenia: {len(warnings)}"
            )
            for warning in warnings:
                print(f"  ! {warning}")
            if args.debug:
                for item in evidence:
                    print(f"  + {item['number']}: {item['description'][:180]}")
                for item in candidates[:10]:
                    print(f"  ? linia {item['line']} ({item['reason']}): {item['text'][:200]}")

        except Exception as exc:
            records.append({"filename": path.name, "status": "ERROR", "error": str(exc)})
            print(f"ERROR: {path.name}: {exc}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nPrzeanalizowano: {len(records)} dokumentów")
    print(f"Wynik zapisano: {args.output}")


if __name__ == "__main__":
    main()
