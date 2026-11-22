"""Files and images you attach to a prompt.

- Images (png, jpg, ...) are sent to the model as pictures. Only models with
  "images": True in peer/config.py MODELS can look at them.
- PDFs: their text is extracted (needs `python -m pip install pypdf` on the
  laptop that attaches them) and added to the prompt like a text file.
- Anything else is read as text (txt, md, csv, json, code, ...) and added to
  the prompt. Other binary files (Word, Excel, zip, audio...) are refused for now.

Every helper laptop receives the attachments too, because helpers never see the
original task.
"""
import base64
import logging
from dataclasses import dataclass, field
from pathlib import Path

from peer.config import IMAGE_EXTENSIONS, MAX_FILE_CHARS, MAX_IMAGE_BYTES, MAX_IMAGES


@dataclass
class Attachments:
    texts: list[tuple[str, str]] = field(default_factory=list)   # (file name, contents)
    images: list[str] = field(default_factory=list)              # base64-encoded images
    image_names: list[str] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)             # "report.pdf (PDF, 12 pages)", ...

    def __bool__(self) -> bool:
        return bool(self.texts or self.images)

    def without_images(self) -> "Attachments":
        """Same attachments minus the pictures (their names are still listed)."""
        return Attachments(self.texts, [], self.image_names, self.summary)

    def preview(self, chars: int) -> "Attachments":
        """The planner only needs to know what each file is, not read all of it (that's slow)."""
        short = [(name, content if len(content) <= chars else
                  content[:chars] + "\n[... preview only: every helper receives the whole file ...]")
                 for name, content in self.texts]
        return Attachments(short, self.images, self.image_names, self.summary)

    def names(self) -> list[str]:
        return [name for name, _ in self.texts] + self.image_names

    def as_text(self) -> str:
        """The text files, ready to append to a prompt."""
        blocks = [f"--- File: {name} ---\n{content}\n--- End of {name} ---" for name, content in self.texts]
        if self.image_names:
            blocks.append("Attached images: " + ", ".join(self.image_names))
        return "\n\n".join(blocks)


def pdf_supported() -> bool:
    try:
        import pypdf  # noqa: F401
        return True
    except ImportError:
        return False


PDF_INSTALL_HINT = "PDF support needs the pypdf library. Install it with:  python -m pip install pypdf"


def read_pdf(path: Path) -> tuple[str, int]:
    """Return (text of every page, number of pages). Raises ValueError if it can't be read."""
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError:
        raise ValueError(PDF_INSTALL_HINT) from None
    logging.getLogger("pypdf").setLevel(logging.ERROR)  # we report problems ourselves
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError(f"{path.name} is password-protected")
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except (PdfReadError, OSError, KeyError, ValueError) as e:
        if isinstance(e, ValueError) and "password" in str(e):
            raise
        raise ValueError(f"couldn't read {path.name} as a PDF ({e})") from None
    if not any(pages):
        raise ValueError(f"{path.name} has no selectable text (probably a scan). "
                         "Try a screenshot of the page with an image model instead.")
    text = "\n\n".join(f"[Page {i + 1}]\n{t}" for i, t in enumerate(pages) if t)
    return text, len(pages)


def load(paths: list[str]) -> Attachments:
    """Read the given files. Raises ValueError with a friendly message if one can't be used."""
    result = Attachments()
    for raw in paths:
        path = Path(raw).expanduser()
        if not path.is_file():
            raise ValueError(f"can't find the file {raw}")
        if path.suffix.lower() in IMAGE_EXTENSIONS:
            data = path.read_bytes()
            if len(data) > MAX_IMAGE_BYTES:
                raise ValueError(f"{path.name} is too big ({len(data) // 1_000_000} MB, max {MAX_IMAGE_BYTES // 1_000_000} MB)")
            if len(result.images) >= MAX_IMAGES:
                raise ValueError(f"too many images (max {MAX_IMAGES})")
            result.images.append(base64.b64encode(data).decode())
            result.image_names.append(path.name)
            result.summary.append(f"{path.name} (image)")
            continue
        if path.suffix.lower() == ".pdf":
            content, pages = read_pdf(path)
            cut = len(content) > MAX_FILE_CHARS
            if cut:
                content = content[:MAX_FILE_CHARS] + "\n[... PDF cut off here, it was too long ...]"
            result.texts.append((path.name, content))
            result.summary.append(f"{path.name} (PDF, {pages} page{'s' if pages != 1 else ''}"
                                  + (", cut off" if cut else "") + ")")
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise ValueError(f"{path.name} isn't a text file, PDF or image (Word/Excel aren't supported yet; "
                             "save it as PDF or copy the text into a .txt file)") from None
        cut = len(content) > MAX_FILE_CHARS
        if cut:
            content = content[:MAX_FILE_CHARS] + "\n[... file cut off here, it was too long ...]"
        result.texts.append((path.name, content))
        result.summary.append(f"{path.name} (text" + (", cut off" if cut else "") + ")")
    return result


def user_message(text: str, att: Attachments | None) -> dict:
    """One chat message with the attachments added: text files appended, images attached."""
    if att:
        text = f"{text}\n\n{att.as_text()}"
    message = {"role": "user", "content": text}
    if att and att.images:
        message["images"] = att.images
    return message
