#!/usr/bin/env python3
"""
Split BARTHOLOMEW_BRAINSTORM_NOTES_VERBATIM.md into manageable chunks.
Preserves conversation boundaries and generates manifest.
"""
import json
import re
from pathlib import Path


MAX_CHARS = 40000


def parse_index(index_path):
    """Parse the index file to extract conversation boundaries."""
    with open(index_path, encoding="utf-8") as f:
        content = f.read()

    # Extract conversation titles and their position markers
    conversations = []
    lines = content.split("\n")
    for line in lines:
        # Match lines like: "1. Echo agent build plan"
        match = re.match(r"^\d+\.\s+(.+)$", line.strip())
        if match:
            title = match.group(1)
            conversations.append({"title": title, "index": len(conversations) + 1})

    return conversations


def split_into_chunks(verbatim_path, index_data, output_dir, max_chars=MAX_CHARS):
    """Split verbatim file into chunks, preserving conversation boundaries."""
    with open(verbatim_path, encoding="utf-8") as f:
        content = f.read()

    # Split by conversation markers (looking for common patterns)
    # The verbatim file likely has conversation separators
    conv_pattern = r"(?=\n# Conversation \d+:|^# Conversation \d+:)"

    # If no explicit markers, split by large gaps or use simple chunking
    conversations = re.split(conv_pattern, content)
    if len(conversations) <= 1:
        # Fallback: split by multiple newlines (paragraph breaks)
        conversations = [content]  # We'll chunk by size

    chunks = []
    current_chunk = []
    current_size = 0
    chunk_num = 1

    # If we have one large blob, split it more intelligently
    if len(conversations) == 1:
        # Split by double newlines (paragraph boundaries)
        paragraphs = content.split("\n\n")

        for para in paragraphs:
            para_size = len(para) + 2  # +2 for the \n\n we removed

            if current_size + para_size > max_chars and current_chunk:
                # Save current chunk
                chunk_content = "\n\n".join(current_chunk)
                chunk_filename = f"chunk_{chunk_num:03d}.md"
                chunks.append(
                    {
                        "filename": chunk_filename,
                        "size": current_size,
                        "start_offset": sum(c["size"] for c in chunks),
                    },
                )

                # Write chunk
                chunk_path = output_dir / chunk_filename
                with open(chunk_path, "w", encoding="utf-8") as f:
                    f.write(chunk_content)

                # Reset for next chunk
                current_chunk = [para]
                current_size = para_size
                chunk_num += 1
            else:
                current_chunk.append(para)
                current_size += para_size

        # Don't forget the last chunk
        if current_chunk:
            chunk_content = "\n\n".join(current_chunk)
            chunk_filename = f"chunk_{chunk_num:03d}.md"
            chunks.append(
                {
                    "filename": chunk_filename,
                    "size": current_size,
                    "start_offset": sum(c["size"] for c in chunks),
                },
            )
            chunk_path = output_dir / chunk_filename
            with open(chunk_path, "w", encoding="utf-8") as f:
                f.write(chunk_content)
    else:
        # We have conversation markers, use those
        for conv in conversations:
            conv_size = len(conv)

            if current_size + conv_size > max_chars and current_chunk:
                # Save current chunk
                chunk_content = "".join(current_chunk)
                chunk_filename = f"chunk_{chunk_num:03d}.md"
                chunks.append(
                    {
                        "filename": chunk_filename,
                        "size": current_size,
                    },
                )

                chunk_path = output_dir / chunk_filename
                with open(chunk_path, "w", encoding="utf-8") as f:
                    f.write(chunk_content)

                current_chunk = [conv]
                current_size = conv_size
                chunk_num += 1
            else:
                current_chunk.append(conv)
                current_size += conv_size

        # Last chunk
        if current_chunk:
            chunk_content = "".join(current_chunk)
            chunk_filename = f"chunk_{chunk_num:03d}.md"
            chunks.append(
                {
                    "filename": chunk_filename,
                    "size": current_size,
                },
            )
            chunk_path = output_dir / chunk_filename
            with open(chunk_path, "w", encoding="utf-8") as f:
                f.write(chunk_content)

    return chunks


def main():
    # Navigate from scripts/brainstorm/ to logs/brainstorm/
    base_dir = Path(__file__).parent.parent.parent / "logs" / "brainstorm"
    verbatim_path = base_dir / "BARTHOLOMEW_BRAINSTORM_NOTES_VERBATIM.md"
    index_path = base_dir / "BARTHOLOMEW_BRAINSTORM_NOTES_INDEX.md"
    output_dir = base_dir / "chunks"

    print(f"Reading index from: {index_path}")
    index_data = parse_index(index_path)
    print(f"Found {len(index_data)} conversations in index")

    print(f"\nReading verbatim notes from: {verbatim_path}")
    verbatim_size = verbatim_path.stat().st_size
    print(f"File size: {verbatim_size:,} bytes")

    print(f"\nSplitting into chunks (max {MAX_CHARS:,} chars each)...")
    chunks = split_into_chunks(verbatim_path, index_data, output_dir, MAX_CHARS)

    # Generate manifest
    manifest = {
        "source_file": "BARTHOLOMEW_BRAINSTORM_NOTES_VERBATIM.md",
        "source_size_bytes": verbatim_size,
        "max_chars_per_chunk": MAX_CHARS,
        "total_chunks": len(chunks),
        "chunks": chunks,
    }

    manifest_path = output_dir / "chunks_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"\n✓ Created {len(chunks)} chunks")
    print(f"✓ Manifest written to: {manifest_path}")
    print("\nChunk summary:")
    for chunk in chunks[:5]:  # Show first 5
        print(f"  - {chunk['filename']}: {chunk['size']:,} chars")
    if len(chunks) > 5:
        print(f"  ... and {len(chunks) - 5} more chunks")


if __name__ == "__main__":
    main()
