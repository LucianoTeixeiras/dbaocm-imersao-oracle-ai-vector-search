"""LRT-06: PDF -> nota pt-BR (PDF sintético, LLM mockado, sem rede)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from knowledge_extractor import pdf_parser as pp
from knowledge_extractor import synthesizer as sy
from knowledge_extractor.note_naming import audit_notes, note_filename_for

EN = (
    "In this chapter we will understand how the agent decides which tool to use. The model receives the question "
    "from the user and, based on the context, it picks the best action for the task. This is important because "
    "the quality of the answer depends on how the data is retrieved and how the prompt is built by the system."
)


def make_pdf(path: Path, pages: list[str]) -> Path:
    """PDF mínimo válido (Helvetica), uma linha de texto por página; '' = página sem texto."""
    objs: list[bytes] = []
    n = len(pages)
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    font_id = 3 + 2 * n
    for i, text in enumerate(pages):
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 9 Tf 20 700 Td ({safe}) Tj ET".encode() if text else b""
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        objs.append(
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
        )
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offs = []
    for i, o in enumerate(objs, start=1):
        offs.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))
    return path


@pytest.fixture
def module(tmp_path: Path) -> Path:
    m = tmp_path / "06-mod"
    (m / "materiais" / "raw").mkdir(parents=True)
    (m / "materiais" / "transcricoes").mkdir(parents=True)
    return m


def test_extract_pages_meta_language(module: Path) -> None:
    pdf = make_pdf(module / "materiais/raw/Module 06 01 Resources.pdf", [EN, "", EN])
    res = pp.extract_pdf(pdf, module / "materiais/transcricoes")
    md = res.output_file.read_text(encoding="utf-8")
    assert "## Página 1" in md and "## Página 3" in md and "agent decides" in md
    meta = json.loads(
        res.output_file.with_suffix(".meta.json").read_text(encoding="utf-8")
    )
    assert (
        meta["source_kind"] == "pdf" and meta["language"] == "en" and meta["pages"] == 3
    )
    assert meta["pages_without_text"] == [2] and meta["scanned"] is False
    assert pp.is_pdf_transcript(res.output_file)
    assert note_filename_for(res.output_file) == "aula-module-06-01-resources.md"


def test_scanned_pdf_warns_and_skips(module: Path, capsys) -> None:
    pdf = make_pdf(module / "materiais/raw/scan.pdf", ["", ""])
    res = pp.extract_pdf(pdf, module / "materiais/transcricoes")
    assert res.scanned and "parece escaneado" in capsys.readouterr().out
    s = sy.LectureSynthesizer.__new__(sy.LectureSynthesizer)
    assert s.process_file(res.output_file, module) is None
    assert not (module / "aulas" / "aula-scan.md").exists()


def test_images_saved_with_limit(tmp_path: Path) -> None:
    big = b"\x89PNG" + b"0" * 4000
    page = SimpleNamespace(
        images=[SimpleNamespace(name=f"im{i}.png", data=big) for i in range(5)]
        + [SimpleNamespace(name="tiny.png", data=b"x")]
    )
    saved = pp._save_page_images(page, 4, tmp_path / "fig", budget=3)
    assert [p.name for p in saved] == ["p004-01.png", "p004-02.png", "p004-03.png"]
    assert (
        pp.page_of_image(saved[0]) == 4
        and sy._image_label(saved[0]) == "página 4"
        and sy._image_mime(saved[0]) == "image/png"
    )


def test_default_figures_dir(module: Path) -> None:
    d = pp._default_figures_dir(
        module / "materiais/transcricoes", Path("Module 06 01 Resources.pdf")
    )
    assert d == module / "materiais/figuras/aula-module-06-01-resources"


def test_split_keeps_pages_whole() -> None:
    md = "# X\n" + "".join(
        f"## Página {i}\n\n{'a' * 100}\n\n---\n" for i in range(1, 11)
    )
    parts = pp.split_pdf_markdown(md, 350)
    assert len(parts) > 1 and parts[0][0] == 1 and parts[-1][1] == 10
    assert all(t.count("## Página") >= 1 for _, _, t in parts)
    assert pp.split_pdf_markdown(md, 10**6) == [(1, 10, md)]


class FakeLLM:
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, synth, text, title, module, fname, info, keyframes=None):
        self.calls.append(
            {
                "text": text,
                "title": title,
                "keyframes": keyframes,
                "prompt": sy.build_system_prompt(
                    module, source_language=synth._source_language
                ),
            }
        )
        return (
            "# Nota\n\nEsta é a nota em português do Brasil sobre como o agente decide qual ferramenta usar, com base no contexto e na pergunta do usuário (p. 1). "
            * 3,
            "gemini:fake",
        )


def _synth(monkeypatch) -> tuple[sy.LectureSynthesizer, FakeLLM]:
    fake = FakeLLM()
    monkeypatch.setattr(
        sy.LectureSynthesizer, "_generate", lambda self, *a, **k: fake(self, *a, **k)
    )
    s = sy.LectureSynthesizer.__new__(sy.LectureSynthesizer)
    s.client = None
    return s, fake


def test_pdf_to_note_small(module: Path, monkeypatch) -> None:
    pdf = make_pdf(module / "materiais/raw/Module 06 01 Resources.pdf", [EN, EN])
    res = pp.extract_pdf(pdf, module / "materiais/transcricoes")
    img = module / "materiais/figuras/aula-module-06-01-resources/p002-01.png"
    img.parent.mkdir(parents=True)
    img.write_bytes(b"\x89PNG")
    meta_p = res.output_file.with_suffix(".meta.json")
    meta = json.loads(meta_p.read_text())
    meta["images"] = ["../figuras/aula-module-06-01-resources/p002-01.png"]
    meta_p.write_text(json.dumps(meta))
    s, fake = _synth(monkeypatch)
    out = s.process_file(res.output_file, module)
    assert out == module / "aulas" / "aula-module-06-01-resources.md" and out.exists()
    call = fake.calls[0]
    assert (
        len(fake.calls) == 1
        and "DOCUMENTO PDF" in call["text"]
        and "(p. N)" in call["text"]
    )
    assert call["keyframes"] == [img.resolve()]
    assert (
        "TRADUZA" in call["prompt"] and "#0F172A" in call["prompt"]
    )  # pt-BR + paleta LRT-02


def test_pdf_large_parts_and_final(module: Path, monkeypatch) -> None:
    pdf = make_pdf(module / "materiais/raw/livro.pdf", [EN] * 6)
    res = pp.extract_pdf(pdf, module / "materiais/transcricoes")
    monkeypatch.setenv("PDF_PART_CHARS", "900")
    s, fake = _synth(monkeypatch)
    s.process_file(res.output_file, module)
    *parts, final = fake.calls
    assert len(parts) >= 2 and all("[PARTE" in c["text"] for c in parts)
    assert (
        "[SÍNTESE FINAL]" in final["text"]
        and "<!-- PARTE 1: páginas 1-" in final["text"]
    )


def test_synthesis_sources_and_audit(module: Path) -> None:
    t = module / "materiais/transcricoes"
    (t / "aula-01-x.txt").write_text("x")
    (t / "notas-soltas.md").write_text("x")  # .md sem meta de PDF: ignorado
    pdf = make_pdf(module / "materiais/raw/Guia.pdf", [EN])
    pp.extract_pdf(pdf, t)
    assert [p.name for p in pp.synthesis_sources(t)] == ["Guia.md", "aula-01-x.txt"]
    faltando = {n for _, n in audit_notes(module)["faltando"]}
    assert "aula-guia.md" in faltando  # PDF reconhecido pela auditoria (check-notes)


def make_pdf_with_images(path: Path, text: str, n_images: int) -> Path:
    """PDF de 1 página com texto + n imagens reais (Pillow gera as imagens, pypdf junta)."""
    import io

    from PIL import Image
    from pypdf import PdfReader, PdfWriter

    page = PdfReader(str(make_pdf(path.with_suffix(".base.pdf"), [text]))).pages[0]
    for i in range(n_images):
        buf = io.BytesIO()
        Image.effect_noise((160, 120), 60 + i).convert("RGB").save(
            buf, format="PDF"
        )  # ruído: > 2 KB
        page.merge_page(PdfReader(io.BytesIO(buf.getvalue())).pages[0])
    w = PdfWriter()
    w.add_page(page)
    with path.open("wb") as f:
        w.write(f)
    return path


def test_real_pdf_images_saved_and_sent_with_limit(module: Path, monkeypatch) -> None:
    monkeypatch.setenv("PDF_MAX_IMAGES", "2")
    pdf = make_pdf_with_images(
        module / "materiais/raw/Module 06 01 Resources.pdf", EN, 3
    )
    res = pp.extract_pdf(pdf, module / "materiais/transcricoes")
    fig_dir = module / "materiais/figuras/aula-module-06-01-resources"
    assert len(res.images) == 2
    assert all(p.parent == fig_dir and p.stat().st_size > 2048 for p in res.images)
    assert (
        len(json.loads(res.output_file.with_suffix(".meta.json").read_text())["images"])
        == 2
    )
    s, fake = _synth(monkeypatch)
    s.process_file(res.output_file, module)
    sent = fake.calls[0]["keyframes"]
    assert {p.resolve() for p in sent} == {p.resolve() for p in res.images}
