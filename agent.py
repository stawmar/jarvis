import json
from pathlib import Path
from uuid import uuid4

import streamlit as st


# ---------------------------------------------------------
# Konfiguracja aplikacji
# ---------------------------------------------------------

st.set_page_config(
    page_title="Kreator raportu",
    layout="wide"
)

BASE_DIR = Path("/srv/homelab/report-agent")
CASES_DIR = BASE_DIR / "cases"
CASES_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------
# Funkcje pomocnicze
# ---------------------------------------------------------

def save_current_form():
    """Zapisuje wartości widocznego formularza bez zmiany statusu zatwierdzenia."""
    if not st.session_state.get("case_file"):
        return

    step = st.session_state.get("step")
    if step == 1:
        fields = {
            "form_case_number": "case_number",
            "form_internal_number": "internal_number",
            "form_for_whom": "for_whom",
            "form_case_description": "case_description",
            "form_questions": "questions",
        }
        for widget_key, state_key in fields.items():
            if widget_key in st.session_state:
                st.session_state[state_key] = st.session_state[widget_key]
        save_case_data()

    elif step == 2:
        index = st.session_state.get("evidence_index", 0)
        evidence = st.session_state.evidence
        if 0 <= index < len(evidence):
            item = evidence[index]
            number = st.session_state.get(f"evidence_number_{index}")
            category = st.session_state.get(f"evidence_category_{index}")
            description = st.session_state.get(f"evidence_description_{index}")

            if number is not None:
                item["number"] = number
            if category is not None:
                if item.get("category") != category:
                    item.pop("measurement", None)
                    item["approved"] = False
                    clear_measurement_widgets(index)
                item["category"] = category
            if description is not None:
                item["description"] = description
            save_case_data()

    elif step == 4:
        index = st.session_state.get("measurement_index", 0)
        evidence = st.session_state.evidence
        if 0 <= index < len(evidence):
            item = evidence[index]
            measurement = dict(item.get("measurement", {}))
            if item.get("category") == "krzaki":
                for widget_key, field in (
                    (f"gross_flower_{index}", "grossFlower"),
                    (f"net_stalk_{index}", "netStalk"),
                    (f"net_root_{index}", "netRoot"),
                ):
                    if widget_key in st.session_state:
                        measurement[field] = st.session_state[widget_key]
            else:
                key = f"gross_weight_{index}"
                if key in st.session_state:
                    measurement["grossWeight"] = st.session_state[key]

            key = f"concentration_{index}"
            if key in st.session_state:
                measurement["substanceConcentration"] = st.session_state[key]
            key = f"tested_{index}"
            if key in st.session_state:
                measurement["tested"] = "y" if st.session_state[key] else ""

            # Zmiana zatwierdzonego pomiaru wymaga ponownego zatwierdzenia.
            old_values = {k: v for k, v in item.get("measurement", {}).items()
                          if k != "approved"}
            new_values = {k: v for k, v in measurement.items() if k != "approved"}
            if old_values != new_values:
                measurement["approved"] = False
            item["measurement"] = measurement
            save_case_data()


def clear_measurement_widgets(index):
    """Czyści wyłącznie pola pomiarowe wskazanego dowodu."""
    for prefix in ("gross_flower_", "net_stalk_", "net_root_",
                   "gross_weight_", "concentration_", "tested_"):
        st.session_state.pop(f"{prefix}{index}", None)


def clear_case_widgets():
    """
    Czyści stan pól formularzy dowodów i pomiarów.
    Nie usuwa danych sprawy ani zapisów w JSON.
    """

    prefixes = (
        "form_",
        "evidence_number_",
        "evidence_category_",
        "evidence_description_",
        "gross_flower_",
        "net_stalk_",
        "net_root_",
        "gross_weight_",
        "concentration_",
        "tested_",
    )

    for key in list(st.session_state.keys()):
        if key.startswith(prefixes):
            del st.session_state[key]

def load_case_data(case_file):
    """
    Wczytuje istniejącą sprawę z JSON
    i odtwarza jej dane w Streamlit.
    """

    case_file = Path(case_file)

    with open(case_file, "r", encoding="utf-8") as f:
        case_data = json.load(f)

    st.session_state.case_id = case_data["caseId"]
    st.session_state.case_file = str(case_file)

    st.session_state.case_number = case_data.get("caseNumber", "")
    st.session_state.internal_number = case_data.get("internalNumber", "")
    st.session_state.for_whom = case_data.get("forWhom", "")
    st.session_state.case_description = case_data.get("case", "")
    st.session_state.questions = case_data.get("questions", "")

    st.session_state.evidence = case_data.get("evidence", [])

    # Po wczytaniu zaczynamy od przeglądu dowodów.
    progress = case_data.get("progress", {})

    st.session_state.step = progress.get("step", 3)
    st.session_state.evidence_index = progress.get("evidenceIndex", 0)
    st.session_state.measurement_index = progress.get("measurementIndex", 0)
    clear_case_widgets()
    for key in ("form_case_number", "form_internal_number", "form_for_whom",
                "form_case_description", "form_questions"):
        st.session_state.pop(key, None)

def save_case_data():
    """
    Aktualizuje istniejący plik JSON sprawy.

    Zapisuje dane sprawy, dowody i pomiary do istniejącego JSON-a.
    """

    if not st.session_state.get("case_file"):
        return

    case_file = Path(st.session_state.case_file)

    if not case_file.exists():
        return

    with open(case_file, "r", encoding="utf-8") as f:
        case_data = json.load(f)

    case_data["caseNumber"] = st.session_state.get("case_number", "")
    case_data["internalNumber"] = st.session_state.get("internal_number", "")
    case_data["forWhom"] = st.session_state.get("for_whom", "")
    case_data["case"] = st.session_state.get("case_description", "")
    case_data["questions"] = st.session_state.get("questions", "")
    case_data["evidence"] = st.session_state.evidence

    with open(case_file, "w", encoding="utf-8") as f:
        json.dump(
            case_data,
            f,
            ensure_ascii=False,
            indent=2
        )

def save_measurement(index, measurement):
    """
    Zapisuje zatwierdzone dane pomiarowe dla konkretnego dowodu.

    Dane trafiają najpierw do stanu aplikacji,
    a następnie cały aktualny stan dowodów zapisujemy
    do pliku case.json.
    """

    st.session_state.evidence[index]["measurement"] = measurement

    save_case_data()

def save_progress():
    """
    Zapisuje aktualny etap kreatora i indeksy dowodów.
    Nie modyfikuje danych merytorycznych sprawy.
    """
    case_file = st.session_state.get("case_file")

    if not case_file:
        return

    case_file = Path(case_file)

    with open(case_file, "r", encoding="utf-8") as f:
        case_data = json.load(f)

    case_data["progress"] = {
        "step": st.session_state.step,
        "evidenceIndex": st.session_state.get("evidence_index", 0),
        "measurementIndex": st.session_state.get("measurement_index", 0),
    }

    with open(case_file, "w", encoding="utf-8") as f:
        json.dump(case_data, f, ensure_ascii=False, indent=2)

def show_sidebar():
    """
    Nawigacja JARVIS-a.

    - Powrót do listy spraw.
    - Wyświetlanie postępu.
    - Przechodzenie do dostępnych etapów.
    - Blokowanie etapów jeszcze niedostępnych.
    """

    current_step = st.session_state.step
    evidence = st.session_state.get("evidence", [])

    has_case = bool(st.session_state.get("case_file"))

    all_evidence_approved = bool(evidence) and all(
        item.get("approved", False)
        for item in evidence
    )

    all_measurements_approved = bool(evidence) and all(
        item.get("measurement", {}).get("approved", False)
        for item in evidence
    )

    steps = [
        (1, "Dane sprawy"),
        (2, "Dowody"),
        (3, "Podsumowanie dowodów"),
        (4, "Dane pomiarowe"),
        (5, "Zdjęcia"),
        (6, "FID"),
        (7, "Podsumowanie"),
        (8, "Raport"),
    ]

    # Dostępność ekranów
    available = {
        1: True,
        2: has_case,
        3: all_evidence_approved,
        4: all_evidence_approved,
        5: False,
        6: False,
        7: False,
        8: False,
    }

    # Faktycznie ukończone etapy
    completed = {
        1: has_case,
        2: all_evidence_approved,
        3: all_evidence_approved,
        4: all_measurements_approved,
        5: False,
        6: False,
        7: False,
        8: False,
    }

    with st.sidebar:

        if st.button(
            "← Lista spraw",
            key="sidebar_back_to_cases",
            use_container_width=True
        ):
            save_current_form()
            save_progress()

            # Czyścimy stan sesji, nie zapisane pliki JSON.
            for key in list(st.session_state.keys()):
                del st.session_state[key]

            st.rerun()

        st.divider()

        st.subheader("Sprawa")

        case_number = st.session_state.get(
            "case_number", "Nowa sprawa"
        )

        st.write(f"**{case_number}**")
        st.divider()

        for step_number, step_name in steps:

            if step_number == current_step:
                label = f"▶ {step_name} (aktualny)"

            elif completed[step_number]:
                label = f"✓ {step_name}"

            else:
                label = f"○ {step_name}"

            if st.button(
                label,
                key=f"sidebar_step_{step_number}",
                disabled=(
                    not available[step_number]
                    or step_number == current_step
                ),
                use_container_width=True
            ):

                save_current_form()

                if step_number == 2:
                    st.session_state.evidence_index = 0
                    st.session_state.edit_return_to = None
                    st.session_state.edit_from_summary = False

                elif step_number == 4:
                    st.session_state.measurement_index = 0

                if step_number != 2:
                    st.session_state.edit_return_to = None
                st.session_state.step = step_number
                save_progress()
                st.rerun()

        st.divider()

        if st.button(
            "Ignoruj sprawę",
            key="sidebar_ignore_case",
            use_container_width=True
        ):
            st.warning(
                "Sprawa zostanie pominięta. "
                "Pliki źródłowe pozostaną bez zmian."
            )

# ---------------------------------------------------------
# Stan kreatora
# ---------------------------------------------------------

if "step" not in st.session_state:
    st.session_state.step = 1


# Każda rozpoczęta sprawa dostaje własne stałe ID techniczne.
# Nie zmienia się ono po poprawieniu sygnatury.
if "case_id" not in st.session_state:
    st.session_state.case_id = uuid4().hex


# ---------------------------------------------------------
# Testowe dane dowodów
# ---------------------------------------------------------
#
# Później tę listę przygotuje agent na podstawie dokumentów,
# zdjęć i pozostałych źródeł.
# ---------------------------------------------------------

if "evidence" not in st.session_state:
    st.session_state.evidence = [
        {
            "number": "1",
            "category": "susz",
            "description": "susz roślinny w folii plastikowej",
            "approved": False
        },
        {
            "number": "2",
            "category": "amfa",
            "description": "substancja proszkowa",
            "approved": False
        },
        {
            "number": "3",
            "category": "",
            "description": "materiał do identyfikacji",
            "approved": False
        }
    ]


if "evidence_index" not in st.session_state:
    st.session_state.evidence_index = 0


if "measurement_index" not in st.session_state:
    st.session_state.measurement_index = 0


# ---------------------------------------------------------
# Wybór zapisanej sprawy
# ---------------------------------------------------------

if "case_file" not in st.session_state:

    st.title("JARVIS — Kreator raportów")

    st.subheader("Rozpocznij pracę")

    saved_cases = sorted(
        CASES_DIR.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )

    if saved_cases:

        case_options = {}

        for case_path in saved_cases:
            try:
                with open(case_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                case_number = data.get("caseNumber", "Bez sygnatury")
                internal_number = data.get("internalNumber", "")

                label = (
                    f"{case_number} | {internal_number} "
                    f"| {case_path.stem[:8]}"
                )

                case_options[label] = case_path

            except (OSError, json.JSONDecodeError):
                continue

        if case_options:
            selected_case = st.selectbox(
                "Wybierz zapisaną sprawę",
                options=list(case_options.keys())
            )

            if st.button("Wznów sprawę", type="primary"):
                load_case_data(case_options[selected_case])
                st.rerun()

    st.divider()

    if st.button("Nowa sprawa"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.session_state.case_id = uuid4().hex
        st.session_state.case_file = None
        st.session_state.step = 1
        st.rerun()

    st.stop()

# ---------------------------------------------------------
# Sidebar aktywnej sprawy
# ---------------------------------------------------------

show_sidebar()

# =========================================================
# KROK 1 — DANE SPRAWY
# =========================================================

if st.session_state.step == 1:

    st.subheader("Dane sprawy")

    # Oddzielamy trwałe dane sprawy od tymczasowych kluczy widgetów.
    # Streamlit usuwa klucze niewidocznych widgetów między ekranami.
    defaults = {
        "form_case_number": st.session_state.get("case_number", "4170-0.Ds.985.2025"),
        "form_internal_number": st.session_state.get("internal_number", "0151/CHR/26"),
        "form_for_whom": st.session_state.get("for_whom", "BES"),
        "form_case_description": st.session_state.get("case_description", ""),
        "form_questions": st.session_state.get("questions", ""),
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

    case_number = st.text_input("Sygnatura sprawy", key="form_case_number")
    internal_number = st.text_input("Numer wewnętrzny", key="form_internal_number")
    for_whom = st.selectbox("Odbiorca raportu", ["BES", "ZLO"], key="form_for_whom")
    case_description = st.text_area("Opis sprawy", key="form_case_description", height=180)
    questions = st.text_area("Pytania", key="form_questions", height=220)

    st.divider()

    # Na pierwszym ekranie nie ma dokąd się cofnąć.
    col_back, col_next = st.columns([1, 1])

    with col_back:
        st.button(
            "← Wstecz",
            disabled=True,
            use_container_width=True,
            key="step1_back"
        )

    with col_next:
        if st.button(
            "Dalej →",
            use_container_width=True,
            key="step1_next"
        ):

            # Zapamiętujemy zaakceptowane dane.
            st.session_state.case_number = case_number
            st.session_state.internal_number = internal_number
            st.session_state.for_whom = for_whom
            st.session_state.case_description = case_description
            st.session_state.questions = questions

            # Sprawdzamy, czy edytujemy istniejącą sprawę.
            existing_file = st.session_state.get("case_file")

            if existing_file:
                # Aktualizujemy istniejącą sprawę.
                # Zachowujemy dowody, pomiary i pozostałe dane.
                case_file = Path(existing_file)

                with open(case_file, "r", encoding="utf-8") as f:
                    case_data = json.load(f)

            else:
                # Tworzymy nową sprawę.
                case_file = CASES_DIR / f"{st.session_state.case_id}.json"

                case_data = {
                    "caseId": st.session_state.case_id,
                    "status": "IN_REVIEW",
                    "evidence": st.session_state.evidence
                }

            # Aktualizujemy tylko dane podstawowe.
            case_data["caseNumber"] = case_number
            case_data["internalNumber"] = internal_number
            case_data["forWhom"] = for_whom
            case_data["case"] = case_description
            case_data["questions"] = questions

            with open(case_file, "w", encoding="utf-8") as f:
                json.dump(
                    case_data,
                    f,
                    ensure_ascii=False,
                    indent=2
                )

            st.session_state.case_file = str(case_file)

            # Pierwszy dowód.
            st.session_state.evidence_index = 0

            st.session_state.step = 2
            save_progress()
            st.rerun()


# =========================================================
# KROK 2 — DOWODY RZECZOWE
# =========================================================

elif st.session_state.step == 2:

    st.subheader("Dowody rzeczowe")

    evidence = st.session_state.evidence
    index = st.session_state.evidence_index
    item = evidence[index]

    # -----------------------------------------------------
    # Dane sprawy — tylko do podglądu
    # -----------------------------------------------------

    with st.expander(
        "Dane sprawy",
        expanded=False
    ):

        st.write(
            f"**Sygnatura:** "
            f"{st.session_state.case_number}"
        )

        st.write(
            f"**Numer wewnętrzny:** "
            f"{st.session_state.internal_number}"
        )

        st.write(
            f"**Odbiorca raportu:** "
            f"{st.session_state.for_whom}"
        )

        st.write("**Opis sprawy:**")

        st.write(
            st.session_state.case_description
            if st.session_state.case_description
            else "—"
        )

        st.write("**Pytania:**")

        st.write(
            st.session_state.questions
            if st.session_state.questions
            else "—"
        )

    st.divider()

    # -----------------------------------------------------
    # Aktualny dowód
    # -----------------------------------------------------

    approved_count = sum(
        1
        for evidence_item in evidence
        if evidence_item["approved"]
    )

    st.write(
        f"**Dowód {index + 1} z {len(evidence)}** "
        f"— zatwierdzono "
        f"{approved_count}/{len(evidence)}"
    )

    number = st.text_input(
        "Numer dowodu",
        value=item["number"],
        key=f"evidence_number_{index}"
    )

    category_options = [
        "",
        "susz",
        "hasz",
        "krzaki",
        "amfa",
        "dop",
        "mdma",
        "flaszki",
        "burn",
        "proch"
    ]

    current_category = (
        item["category"]
        if item["category"] in category_options
        else ""
    )

    category = st.selectbox(
        "Kategoria",
        category_options,
        index=category_options.index(
            current_category
        ),
        key=f"evidence_category_{index}"
    )

    description = st.text_area(
        "Opis dowodu",
        value=item["description"],
        height=120,
        key=f"evidence_description_{index}"
    )

    if item["approved"]:
        st.success(
            "Ten dowód został zatwierdzony."
        )

    st.divider()

    # -----------------------------------------------------
    # Nawigacja
    # -----------------------------------------------------

    col_back, col_next = st.columns([1, 1])

    with col_back:

        if st.button(
            "← Wstecz",
            use_container_width=True,
            key="step2_back"
        ):

            save_current_form()

            # Jeżeli weszliśmy tutaj z podsumowania
            # przez "Edytuj", wracamy do podsumowania.
            if st.session_state.get(
                "edit_from_summary",
                False
            ):
                st.session_state.edit_from_summary = False
                st.session_state.step = 3

            # Poprzedni dowód.
            elif index > 0:
                st.session_state.evidence_index -= 1

            # Pierwszy dowód -> dane sprawy.
            else:
                st.session_state.step = 1

            save_progress()
            st.rerun()

    with col_next:

        if st.button(
            "Dalej →",
            use_container_width=True,
            key="step2_next"
        ):

            # Jeśli zmieniono kategorię, dotychczasowe pomiary
            # mogą nie odpowiadać nowemu rodzajowi dowodu.
            if item["category"] != category:
                item.pop("measurement", None)
                clear_measurement_widgets(index)

            # Zapisujemy zaakceptowane dane dowodu.
            item["number"] = number
            item["category"] = category
            item["description"] = description
            item["approved"] = True

            # Aktualizujemy JSON.
            save_case_data()

            # Jeżeli weszliśmy do edycji z innego ekranu,
            # po zapisaniu wracamy dokładnie tam, skąd przyszliśmy.
            edit_return_to = st.session_state.get(
                "edit_return_to"
            )

            if edit_return_to == "summary":

                st.session_state.edit_return_to = None
                st.session_state.step = 3

            elif edit_return_to == "measurements":

                st.session_state.edit_return_to = None

                # Wracamy do tego samego dowodu
                # na ekranie danych pomiarowych.
                st.session_state.measurement_index = index
                st.session_state.step = 4

            elif index < len(evidence) - 1:

                st.session_state.evidence_index += 1

            else:

                st.session_state.step = 3

            save_progress()
            st.rerun()


# =========================================================
# KROK 3 — PODSUMOWANIE DOWODÓW
# =========================================================

elif st.session_state.step == 3:

    st.subheader("Podsumowanie dowodów")

    evidence = st.session_state.evidence

    approved_count = sum(
        1
        for item in evidence
        if item["approved"]
    )

    st.write(
        f"Zatwierdzono **{approved_count} "
        f"z {len(evidence)}** dowodów."
    )

    st.divider()

    # -----------------------------------------------------
    # Lista dowodów
    # -----------------------------------------------------

    for index, item in enumerate(evidence):

        status = (
            "✓"
            if item["approved"]
            else "⚠"
        )

        col_info, col_edit = st.columns(
            [5, 1]
        )

        with col_info:

            st.write(
                f"**{status} Dowód {item['number']}** — "
                f"{item['category'] if item['category'] else 'brak kategorii'}"
            )

            st.caption(item["description"])

        with col_edit:

            if st.button(
                "Edytuj",
                key=f"edit_evidence_{index}",
                use_container_width=True
            ):
                # Po edycji wracamy do podsumowania dowodów.
                st.session_state.edit_return_to = "summary"

                st.session_state.evidence_index = index
                st.session_state.step = 2

                save_progress()
                st.rerun()

    st.divider()

    # -----------------------------------------------------
    # Nawigacja
    # -----------------------------------------------------

    col_back, col_next = st.columns([1, 1])

    with col_back:

        if st.button(
            "← Wstecz",
            use_container_width=True,
            key="step3_back"
        ):

            # Wracamy do ostatniego dowodu.
            st.session_state.evidence_index = (
                len(evidence) - 1
            )

            st.session_state.step = 2
            save_progress()
            st.rerun()

    with col_next:

        if st.button(
            "Dalej →",
            use_container_width=True,
            key="step3_next"
        ):

            all_approved = all(
                item["approved"]
                for item in evidence
            )

            if all_approved:

                # Dane pomiarowe zaczynamy
                # od pierwszego dowodu.
                st.session_state.measurement_index = 0

                st.session_state.step = 4
                save_progress()
                st.rerun()

            else:

                st.error(
                    "Nie wszystkie dowody "
                    "zostały zatwierdzone."
                )


# =========================================================
# KROK 4 — DANE POMIAROWE
# =========================================================

elif st.session_state.step == 4:

    st.subheader("Dane pomiarowe")

    evidence = st.session_state.evidence
    index = st.session_state.measurement_index
    item = evidence[index]

    # Pobieramy wcześniej zapisane dane pomiarowe.
    # Przy pierwszej wizycie na tym dowodzie będzie to pusty słownik
    measurement = item.get("measurement", {})

    # Informacje o aktualnym dowodzie oraz możliwość
    # poprawienia jego danych bez wychodzenia z tego etapu.
    col_info, col_edit = st.columns([5, 1])

    # -----------------------------------------------------
    # Podsumowanie aktualnego dowodu
    # -----------------------------------------------------
    #
    # Pokazujemy dane zatwierdzone na wcześniejszym etapie.
    # Dzięki temu przed wpisaniem pomiarów użytkownik
    # ponownie weryfikuje, którego dowodu dotyczą dane.
    # -----------------------------------------------------

    st.write(
        f"**Dowód {index + 1} z {len(evidence)}**"
    )

    with st.container(border=True):

        col_info, col_edit = st.columns([5, 1])

        with col_info:
            st.write(
                f"**Numer dowodu:** {item['number']}"
            )

            st.write(
                f"**Kategoria:** "
                f"{item['category'] if item['category'] else 'brak kategorii'}"
            )

            st.write("**Opis dowodu:**")

            st.write(
                item["description"]
                if item["description"]
                else "—"
            )

        with col_edit:
            if st.button(
                    "Edytuj dowód",
                    key=f"edit_evidence_from_measurement_{index}",
                    use_container_width=True
            ):
                # Zapamiętujemy, że po edycji mamy wrócić
                # do danych pomiarowych tego samego dowodu.
                save_current_form()
                st.session_state.edit_return_to = "measurements"

                st.session_state.evidence_index = index
                st.session_state.step = 2

                save_progress()
                st.rerun()

    st.divider()

    # -----------------------------------------------------
    # KRZAKI -> docelowo arkusz genBushes
    # -----------------------------------------------------

    if item["category"] == "krzaki":

        gross_flower = st.number_input(
            "grossFlower",
            min_value=0.0,
            value=float(measurement.get("grossFlower", 0.0)),
            step=0.01,
            key=f"gross_flower_{index}"
        )

        net_stalk = st.number_input(
            "netStalk",
            min_value=0.0,
            value=float(measurement.get("netStalk", 0.0)),
            step=0.01,
            key=f"net_stalk_{index}"
        )

        net_root = st.number_input(
            "netRoot",
            min_value=0.0,
            value=float(measurement.get("netRoot", 0.0)),
            step=0.01,
            key=f"net_root_{index}"
        )

    # -----------------------------------------------------
    # Pozostałe kategorie -> docelowo arkusz generator
    # -----------------------------------------------------

    else:

        gross_weight = st.number_input(
            "grossWeight",
            min_value=0.0,
            value=float(measurement.get("grossWeight", 0.0)),
            step=0.01,
            key=f"gross_weight_{index}"
        )

    # -----------------------------------------------------
    # Dane wspólne
    # -----------------------------------------------------

    substance_concentration = st.number_input(
        "Stężenie",
        min_value=0.0,
        value=float(measurement.get("substanceConcentration", 0.0)),
        step=0.01,
        key=f"concentration_{index}"
    )

    # Domyślnie dowód jest badany.
    tested_checked = st.checkbox(
        "Badany",
        value=(
            measurement.get("tested") == "y"
            if measurement
            else True
        ),
        key=f"tested_{index}"
    )

    # Dokładny format wymagany później przez Excel:
    # zaznaczony   -> "y"
    # odznaczony   -> pusta komórka
    tested = "y" if tested_checked else ""

    st.divider()

    # -----------------------------------------------------
    # Nawigacja
    # -----------------------------------------------------

    col_back, col_next = st.columns([1, 1])

    with col_back:

        if st.button(
            "← Wstecz",
            use_container_width=True,
            key="step4_back"
        ):

            save_current_form()

            # Poprzedni dowód pomiarowy.
            if index > 0:
                st.session_state.measurement_index -= 1

            # Pierwszy dowód -> podsumowanie dowodów.
            else:
                st.session_state.step = 3

            save_progress()
            st.rerun()

    with col_next:

        if st.button(
                "Dalej →",
                use_container_width=True,
                key="step4_next"
        ):

            # -------------------------------------------------
            # Budujemy dane pomiarowe zależnie od kategorii.
            # -------------------------------------------------

            if item["category"] == "krzaki":

                measurement = {
                    "grossFlower": gross_flower,
                    "netStalk": net_stalk,
                    "netRoot": net_root,
                    "substanceConcentration": substance_concentration,
                    "tested": tested,
                    "approved": True
                }

            else:

                measurement = {
                    "grossWeight": gross_weight,
                    "substanceConcentration": substance_concentration,
                    "tested": tested,
                    "approved": True
                }

            # Zapisujemy pomiary do konkretnego dowodu
            # oraz aktualizujemy case.json.
            save_measurement(
                index,
                measurement
            )

            # -------------------------------------------------
            # Nawigacja
            # -------------------------------------------------

            if index < len(evidence) - 1:

                st.session_state.measurement_index += 1
                save_progress()
                st.rerun()

            else:

                save_progress()
                st.success(
                    "Wszystkie dane pomiarowe zostały zatwierdzone."
                )
