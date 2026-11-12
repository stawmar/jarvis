#!/usr/bin/env python3
"""JARVIS — robocze połączenie danych z OCR i FID.

Wejście:
  document_scan.json       (wynik document_scanner.py)
  cases/fid_scan.json      (wynik fid_scanner.py)
Wyjście:
  case_import_preview.json

Nie zmienia agent.py, plików wejściowych ani istniejących spraw.
Nie wyciąga wniosku DOP z braku chromatogramu i nie zgaduje mas brutto.
"""

import json
from collections import Counter, defaultdict
from pathlib import Path

DOCUMENTS_FILE = Path("document_scan.json")
FID_FILE = Path("cases/fid_scan.json")
BIOLOGICAL_FILE = Path("cases/biological_scan.json")
OUTPUT_FILE = Path("case_import_preview.json")


def read_json(path):
    if not path.is_file():
        raise FileNotFoundError(
            f"Brak pliku: {path}. Uruchom najpierw odpowiedni skaner."
        )
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def evidence_id(row):
    value = row.get("evidenceNumber")
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip().replace(",", ".")


def combine(documents, fid):
    if not isinstance(documents, list):
        raise ValueError("document_scan.json musi być listą dokumentów")
    if not isinstance(fid, dict):
        raise ValueError("cases/fid_scan.json musi być obiektem JSON")

    chromatograms = fid.get("chromatograms", [])
    concentrations = fid.get("concentrations", [])
    if not isinstance(chromatograms, list) or not isinstance(concentrations, list):
        raise ValueError("Pola chromatograms i concentrations muszą być listami")

    chrom_by_case = defaultdict(list)
    conc_by_case = defaultdict(list)
    for row in chromatograms:
        chrom_by_case[row.get("caseNumber")].append(row)
    for row in concentrations:
        conc_by_case[row.get("caseNumber")].append(row)

    output_cases = []
    seen = set()
    for doc in documents:
        if doc.get("status") != "OK":
            continue
        extracted = doc.get("data") or {}
        case_number = extracted.get("caseNumber")
        if not case_number:
            print(f"UWAGA: brak numeru sprawy: {doc.get('filename')}")
            continue

        charts = chrom_by_case[case_number]
        values = conc_by_case[case_number]
        count_matches = len(charts) == len(values)
        identifiers_match = Counter(map(evidence_id, charts)) == Counter(
            map(evidence_id, values)
        )

        warnings = []
        if case_number in seen:
            warnings.append("Powtórzony numer sprawy w dokumentach OCR")
        seen.add(case_number)
        if not count_matches:
            warnings.append("Różna liczba chromatogramów i stężeń")
        if not identifiers_match:
            warnings.append("Niezgodne oznaczenia próbek w FID")

        evidence = []
        for item in extracted.get("evidence", []):
            evidence.append({
                "number": str(item.get("number", "")),
                "description": item.get("description"),
                "grossWeight": None,  # do uzupełnienia ze skanu okładki
                "approved": False,
            })

        fid_results = []
        for row in values:
            sample = evidence_id(row)
            fid_results.append({
                "evidenceNumber": sample,
                "substance": row.get("substance"),
                "concentrationPercent": row.get("concentrationPercent"),
                "fidIdentifier": row.get("fidIdentifier"),
                "sourceFile": row.get("sourceFile"),
                "needsEvidenceAssignment": sample is None,
                "approved": False,
            })

        output_cases.append({
            "caseNumber": case_number,
            "internalNumber": extracted.get("internalNumber"),
            "source": extracted.get("source"),
            "sourceDocument": doc.get("filename"),
            "evidenceFromOcr": evidence,
            "fid": {
                "chromatogramCount": len(charts),
                "concentrationCount": len(values),
                "countMatches": count_matches,
                "identifiersMatch": identifiers_match,
                "results": fid_results,
            },
            "warnings": warnings,
            "approved": False,
        })

    unmatched = sorted(
        ((set(chrom_by_case) | set(conc_by_case)) - seen) - {None}
    )
    return {"cases": output_cases, "fidCasesWithoutDocument": unmatched}

def prepare_biological_cases(data):
    # Sprawdzamy strukturę danych otrzymanych ze skanera.
    if not isinstance(data, dict) or not isinstance(data.get("cases"), list):
        raise ValueError("Nieprawidłowa struktura biological_scan.json")

    result = []

    for case in data["cases"]:
        # Zachowujemy wszystkie dane rozpoznane przez skaner.
        item = case.copy()

        # Pobieramy numery próbek z dokumentów.
        samples = case.get("sampleIdentifiers") or []
        post_samples = case.get("postSampleIdentifiers") or []

        # Lista problemów przypisanych do pól formularza.
        issues = []

        # Brak rozpoznanego numeru próbki.
        if not samples:
            issues.append({
                "field": "sampleIdentifiers",
                "status": "MISSING",
                "message": "Nie odczytano numeru próbki. Sprawdź okładkę."
            })

        # Numery występujące w postanowieniu, ale nieprzypisane
        # do rozpoznanych próbek.
        extra = sorted(set(post_samples) - set(samples))

        if samples and extra:
            issues.append({
                "field": "sampleIdentifiers",
                "status": "CONFLICT",
                "message": (
                    "Postanowienie zawiera dodatkowe lub inne numery: "
                    + ", ".join(extra)
                )
            })

        # Analizujemy ostrzeżenia wygenerowane przez skaner biologiczny.
        for warning in case.get("warnings", []):

            # Nie udało się ustalić wariantu badania narkotykowego.
            if "Nie ustalono wariantu procedury narkotykowej" in warning:
                issues.append({
                    "field": "procedureVariant",
                    "status": "MISSING",
                    "message": warning
                })

            # Procedura została rozpoznana tylko na podstawie nazwy pliku.
            # Nie traktujemy jej jako potwierdzonej.
            elif "Wariant narko wywnioskowany tylko z nazwy pliku" in warning:
                issues.append({
                    "field": "procedureVariant",
                    "status": "REVIEW",
                    "message": warning
                })

        # Informacje dla przyszłego formularza GUI.
        item["fieldIssues"] = issues

        # Każda sprawa wymaga zatwierdzenia przez użytkownika.
        item["approved"] = False

        result.append(item)

    return result

def main():
    preview = combine(read_json(DOCUMENTS_FILE), read_json(FID_FILE))

    # Dołączamy sprawy biologiczne do podglądu importu.
    # Nie przekazujemy ich do FID — to osobna ścieżka.
    preview["biologicalCases"] = prepare_biological_cases(
        read_json(BIOLOGICAL_FILE)
    )

    # Wyświetlamy wykryte problemy z polami biologicznymi.
    for case in preview["biologicalCases"]:
        for issue in case["fieldIssues"]:
            print(f"  [{issue['status']}] {issue['message']}")

    with OUTPUT_FILE.open("w", encoding="utf-8") as stream:
        json.dump(preview, stream, ensure_ascii=False, indent=2)

    for case in preview["cases"]:
        report = case["fid"]
        status = (
            "OK"
            if report["countMatches"] and report["identifiersMatch"]
            else "UWAGA"
        )

        print(
            f"{case['caseNumber']} | "
            f"chromatogramy: {report['chromatogramCount']} | "
            f"stężenia: {report['concentrationCount']} | {status}"
        )

        for warning in case["warnings"]:
            print("  ! " + warning)

    if preview["fidCasesWithoutDocument"]:
        print(
            "FID bez dokumentu OCR:",
            ", ".join(preview["fidCasesWithoutDocument"])
        )

    print(f"\nSprawy: {len(preview['cases'])}. Zapisano: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
