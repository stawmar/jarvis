#!/usr/bin/env python3
"""Ostrożny odczyt dowodów rzeczowych z dokumentów OCR (DOCX).

Zgodność z wcześniejszym importerem:
- wynik: lista rekordów z filename/status/data/ocrText/approved,
- data.evidence: lista {number, description},
- brak automatycznego tworzenia dowodów z nazw plików.

Dodatkowe pola warnings i reviewCandidates służą diagnostyce.
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
    "/srv/data/nextcloud/data/marek/files/"
    "Raporty/JARVIS_TEST/Po_OCR"
)
OUTPUT_FILE = Path("document_scan.json")

# Numery dowodów: np. 1, 1.1, 1A1, 1.1A, HA18478781.
# Świadomie nie zakładamy, że każdy numer koperty jest numerem dowodu.
EVIDENCE_ID = r"(?:\d+(?:[.,]\d+)*(?:[A-Za-z]\d*)?|[A-Za-z]{1,5}[- ]?\d{4,}(?:[-/]\d+)*)"

# Mocne oznaczenia konkretnego dowodu. Nie dopasowujemy gołego „nr”.
EXPLICIT_EVIDENCE = re.compile(
    rf"(?<!\w)(?:"
    rf"ślad\s*/\s*przedmiot|slad\s*/\s*przedmiot|"
    rf"ślad|slad|przedmiot|"
    rf"dow[oó]d(?:\s+rzeczowy)?|"
    rf"d\s*\.?\s*rz\.?"
    rf")\s*(?:nr\.?|numer|o\s+numerze)\s*[:#-]?\s*"
    rf"(?P<number>{EVIDENCE_ID})(?=$|[\s,;:()\]–—-])",
    re.IGNORECASE,
)

# Numerowana pozycja jest dowodem TYLKO w sekcji materiału dowodowego.
NUMBERED_ITEM = re.compile(
    rf"^\s*(?:[-•*]\s*)?(?P<number>\d+(?:\.\d+)*(?:[A-Za-z]\d*)?)"
    rf"(?:\s*[).:\-–—]\s*|\s+\|\s*|\s+(?=[A-Za-zĄĆĘŁŃÓŚŹŻ]))"
    rf"(?P<description>.+?)\s*$",
    re.IGNORECASE,
)

CASE_NUMBER = re.compile(r"\b\d{4}-\d+\.Ds\.\d+\.\d{4}(?:\.[A-Za-z])?\b", re.I)
INTERNAL_NUMBER = re.compile(r"\b\d{4}/CHR/\d{2}\b", re.I)
ORDER_HEADING = re.compile(r"^\s*P?OSTANOWIENIE\b", re.I)

# Początek listy dowodów. Samo słowo „dowód” nie wystarcza.
EVIDENCE_HEADINGS = (
    "material dowodowy", "material do badan", "material przekazany do badan",
    "przekazany do badan material", "wykaz dowodow", "wykaz sladow",
    "dowody rzeczowe", "przedmioty przekazane do badan", "wykaz probek",
    "opis dostarczonego materialu", "opis materialu dowodowego",
)
END_HEADINGS = (
    "wyniki badan", "wyniki analiz", "metodyka badan", "metody badan",
    "przebieg badan", "wnioski", "pytania do bieglego", "opinia", "postanowienie",
    "oswiadczenie zleceniobiorcy", "oswiadczenie zleceniodawcy",
)
EVIDENCE_WORDS = re.compile(
    r"\b(susz|roslinn\w*|amfetamin\w*|metamfetamin\w*|haszysz\w*|"
    r"tablet\w*|prosz\w*|krysztal\w*|worecz\w*|dopala\w*|"
    r"mdma|substancj\w*|dowod\w*|slad\w*|przedmiot\w*)\b",
    re.I,
)
PHYSICAL_FILENAME_HINT = re.compile(r"\b(susz|amfa|dop|hasz|mdma)\b", re.I)


def ascii_text(value):
    """Wersja do porównań, bez zmieniania oryginalnych opisów dowodów."""
    value = value.replace("ł", "l").replace("Ł", "L")
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(ch)
    ).lower()


def clean_line(value):
    """Normalizacja odstępów/OCR bez usuwania polskich znaków."""
    value = value.replace("\xa0", " ").replace("\u200b", "")
    value = value.replace("\t", " ")
    return re.sub(r"[ ]{2,}", " ", value).strip()


def read_docx(path):
    """Czyta akapity i tabele w kolejności z DOCX (nie: osobno akapity, potem tabele)."""
    document = Document(path)
    lines = []

    for element in document.element.body.iterchildren():
        if element.tag == qn("w:p"):
            paragraph = Paragraph(element, document)
            line = clean_line(paragraph.text)
            if line:
                lines.append(line)
        elif element.tag == qn("w:tbl"):
            table = Table(element, document)
            for row in table.rows:
                # Komórki pozostają oddzielone znakiem | do analizy tabel.
                cells = [clean_line(cell.text.replace("\n", " ")) for cell in row.cells]
                if any(cells):
                    lines.append(" | ".join(cells))

    return "\n".join(lines)


def is_evidence_heading(line):
    norm = ascii_text(line)
    return any(fragment in norm for fragment in EVIDENCE_HEADINGS)


def is_end_heading(line):
    norm = ascii_text(line).strip(" :-")
    return any(norm.startswith(prefix) for prefix in END_HEADINGS)


def split_report_from_order(text):
    """Postanowienie jest kontekstem; numery z pytań nie stają się automatycznie dowodami."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if i > 0 and ORDER_HEADING.match(line):
            return lines[:i], lines[i:]
    return lines, []


def extract_evidence(text):
    """Zwraca (dowody, ostrzeżenia, fragmenty_do_przejrzenia).

    Źródło numerów: jawne „ślad/przedmiot nr X” lub numerowana pozycja
    w sekcji dowodów. Nie używa nazwy pliku ani ilości woreczków.
    """
    report_lines, order_lines = split_report_from_order(text)
    found = OrderedDict()
    warnings = []
    candidates = []
    in_section = False
    sections_found = 0

    # Poszerzamy opisy o jedną linię po oznaczeniu bez opisu.
    pending_number = None
    pending_line = None

    def add_evidence(number, description, line_no):
        number = clean_line(number)
        description = clean_line(description).strip(" -–—:;|")
        if not description:
            return False  # Bez opisu nie tworzymy pozornie kompletnego dowodu.

        if number in found:
            previous = found[number]["description"]
            if ascii_text(previous) != ascii_text(description):
                warnings.append(
                    f"Powtórzony numer dowodu {number} z innym opisem — sprawdź OCR"
                )
                candidates.append({"line": line_no, "text": f"{number}: {description[:220]}"})
            return True

        found[number] = {"number": number, "description": description[:1500]}
        return True

    for line_no, raw in enumerate(report_lines, start=1):
        line = clean_line(raw)
        if not line:
            continue

        if is_evidence_heading(line):
            in_section = True
            sections_found += 1
            pending_number = None
            pending_line = None
            # Nagłówek może zawierać też zawartość po dwukropku.
            match = re.search(r":\s*(.+)$", line)
            if not match:
                continue
            line = match.group(1)

        elif is_end_heading(line):
            if pending_number is not None:
                warnings.append(f"Numer dowodu {pending_number} bez opisu — do weryfikacji")
            in_section = False
            pending_number = None
            pending_line = None

        # Samotne „Ślad/przedmiot nr 2” może mieć opis w kolejnym wierszu.
        # Akceptujemy tylko bezpośrednio następujący wiersz o treści rzeczowej.
        if pending_number is not None and not is_end_heading(line):
            if (line_no == pending_line + 1
                    and EVIDENCE_WORDS.search(ascii_text(line))
                    and not EXPLICIT_EVIDENCE.search(line)):
                add_evidence(pending_number, line, line_no)
            else:
                warnings.append(f"Numer dowodu {pending_number} bez jednoznacznego opisu")
            pending_number = None
            pending_line = None

        # Jawnie opisany dowód — może występować poza tabelą/sekcją.
        explicit = list(EXPLICIT_EVIDENCE.finditer(line))
        if explicit:
            pending_number = None
            for index, match in enumerate(explicit):
                end = explicit[index + 1].start() if index + 1 < len(explicit) else len(line)
                description = line[match.end():end].strip(" -–—:;|")
                number = match.group("number")
                if not add_evidence(number, description, line_no):
                    pending_number = number
                    pending_line = line_no
            continue

        # W sekcji materiału dowodowego pozycje mogą być numerowane 1., 1.1, 1A1 itd.
        if in_section:
            numbered = NUMBERED_ITEM.match(line)
            if numbered and not re.match(
                    r"^(czy|jak|kiedy|gdzie|dlaczego|w\s+jaki\s+sposob)\b",
                    ascii_text(numbered.group("description")), re.I
            ):
                pending_number = None
                number = numbered.group("number")
                description = numbered.group("description")
                if not add_evidence(number, description, line_no):
                    pending_number = number
                    pending_line = line_no
                continue

        # Utrwalamy podejrzane fragmenty tylko do kontroli, nie jako dowody.
        if (in_section and EVIDENCE_WORDS.search(ascii_text(line))) or re.search(
                r"\bnr\.?\s+[A-Z]{1,5}\d{4,}\b", line, re.I
        ):
            if len(candidates) < 20:
                candidates.append({"line": line_no, "text": line[:300]})

    # Numery opakowań i środków w postanowieniu mogą pomóc człowiekowi,
    # lecz nie są samodzielnym dowodem, jeśli brak mocnego oznaczenia.
    if not found and order_lines:
        for line_no, line in enumerate(order_lines, start=len(report_lines) + 1):
            if EVIDENCE_WORDS.search(ascii_text(line)) and len(candidates) < 20:
                candidates.append({"line": line_no, "text": clean_line(line)[:300]})

    if pending_number:
        warnings.append(f"Numer dowodu {pending_number} bez opisu — do weryfikacji")
    if sections_found and not found and candidates:
        warnings.append("Znaleziono fragmenty materiału dowodowego, ale bez jednoznacznych pozycji")
    elif found and candidates:
        warnings.append("Są dodatkowe niejednoznaczne fragmenty — sprawdź kompletność dowodów")

    return list(found.values()), list(dict.fromkeys(warnings)), candidates


def extract_data(text, filename=""):
    """Zachowuje pierwotny kontrakt pola data dla case_importer.py."""
    case_match = CASE_NUMBER.search(text)
    internal_match = INTERNAL_NUMBER.search(text)
    evidence, _, _ = extract_evidence(text)

    upper = ascii_text(text[:2500])
    stem = filename.upper()
    if "biuro ekspertyz sadowych" in upper:
        source = "BES"
    elif "zespol lekarzy opiniujacych" in upper:
        source = "ZLO"
    elif stem.startswith("BES"):
        source = "BES"  # Wskazówka z nazwy dokumentu, nie z treści.
    elif stem.startswith("ZLO"):
        source = "ZLO"
    else:
        source = None

    return {
        "source": source,
        "caseNumber": case_match.group(0) if case_match else None,
        "internalNumber": internal_match.group(0) if internal_match else None,
        "evidence": evidence,
    }


def main():
    parser = argparse.ArgumentParser(description="Skaner dowodów rzeczowych JARVIS")
    parser.add_argument("--source", type=Path, default=SOURCE_DIR,
                        help="Katalog z DOCX po OCR")
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE,
                        help="Plik JSON z wynikami")
    parser.add_argument("--debug", action="store_true",
                        help="Pokaż przykładowe fragmenty wymagające kontroli")
    args = parser.parse_args()

    if not args.source.is_dir():
        parser.error(f"Nie istnieje katalog: {args.source}")

    results = []
    paths = sorted(args.source.glob("*.docx"))
    if not paths:
        print(f"UWAGA: Brak plików DOCX w {args.source}")

    for path in paths:
        try:
            content = read_docx(path)
            data = extract_data(content, path.name)
            _, warnings, candidates = extract_evidence(content)

            # Nazwa pliku uruchamia tylko ostrzeżenie, nie generuje dowodów.
            if not data["evidence"] and PHYSICAL_FILENAME_HINT.search(ascii_text(path.stem)):
                warnings.append("Nazwa pliku sugeruje materiał rzeczowy; nie odczytano pewnych dowodów")

            record = {
                "filename": path.name,
                "status": "OK",  # Brak rozpoznanych dowodów nie jest błędem wykonania.
                "data": data,
                "ocrText": content,
                "approved": False,
                "warnings": warnings,
                "reviewCandidates": candidates,
            }
            results.append(record)

            print(
                f"{path.name} | "
                f"sprawa: {data['caseNumber']} | "
                f"wew: {data['internalNumber']} | "
                f"dowody: {len(data['evidence'])} | "
                f"ostrzeżenia: {len(warnings)}"
            )
            for warning in warnings:
                print(f"  ! {warning}")
            if args.debug:
                for evidence in data["evidence"]:
                    print(f"  + {evidence['number']}: {evidence['description'][:150]}")
                for candidate in candidates[:5]:
                    print(f"  ? linia {candidate['line']}: {candidate['text'][:150]}")

        except Exception as exc:
            results.append({"filename": path.name, "status": "ERROR", "error": str(exc)})
            print(f"ERROR: {path.name}: {exc}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nPrzeanalizowano: {len(results)} dokumentów")
    print(f"Wynik zapisano: {args.output}")


if __name__ == "__main__":
    main()
