"""Files and images you attach to a prompt.

- Images (png, jpg, ...) are sent to the model as pictures. Only models with
  "images": True in peer/config.py MODELS can look at them.
- Anything else is read as text (txt, md, csv, json, code, ...) and added to
  the prompt. Files that aren't text (e.g. PDF, Word) are refused for now.

Every helper laptop receives the attachments too, because helpers never see the
original task.
"""
import base64
from dataclasses import dataclass, field
from pathlib import Path

from peer.config import IMAGE_EXTENSIONS, MAX_FILE_CHARS, MAX_IMAGE_BYTES, MAX_IMAGES


@dataclass
class Attachments:
    texts: list[tuple[str, str]] = field(default_factory=list)   # (file name, contents)
    images: list[str] = field(default_factory=list)              # base64-encoded images
    image_names: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.texts or self.images)

    def names(self) -> list[str]:
        return [name for name, _ in self.texts] + self.image_names

    def as_text(self) -> str:
        """The text files, ready to append to a prompt."""
        blocks = [f"--- File: {name} ---\n{content}\n--- End of {name} ---" for name, content in self.texts]
        if self.image_names:
            blocks.append("Attached images: " + ", ".join(self.image_names))
        return "\n\n".join(blocks)


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
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise ValueError(f"{path.name} isn't a text file or an image (PDF/Word aren't supported yet; "
                             "copy the text into a .txt file instead)") from None
        if len(content) > MAX_FILE_CHARS:
            content = content[:MAX_FILE_CHARS] + "\n[... file cut off here, it was too long ...]"
        result.texts.append((path.name, content))
    return result


def user_message(text: str, att: Attachments | None) -> dict:
    """One chat message with the attachments added: text files appended, images attached."""
    if att:
        text = f"{text}\n\n{att.as_text()}"
    message = {"role": "user", "content": text}
    if att and att.images:
        message["images"] = att.images
    return message
