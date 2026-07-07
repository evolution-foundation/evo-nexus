import json, subprocess, pathlib, sys
root = pathlib.Path('/home/sistemabritto/Documentos/evo-nexus')
skill = root/'.claude/skills/ai-image-creator/scripts/generate-image.py'
items = json.loads((root/'workspace/assets/prompts/zapclub_group_covers.json').read_text())
outdir = root/'workspace/assets/images/zapclub-groups'
outdir.mkdir(parents=True, exist_ok=True)
base_style = """
Square WhatsApp community/group cover art for the brand ZapClub, tagline IA PARA NEGÓCIOS.
Visual identity: premium dark charcoal/black background, vibrant neon green #00FFA7, crisp white, modern tech startup aesthetic, clean vector + 3D hybrid, high contrast, centered composition, subtle glow, speech-bubble motif, upward analytics bars, tiny floating data squares, elegant sans-serif typography. Portuguese text must be perfectly spelled and readable.
Do not use mockups, people, watermarks, distorted letters, random extra words, or clutter.
""".strip()
# Use the already generated successful test for the first group.
test = outdir/'test-image2.png'
first = outdir/'conquistador-de-tokens.png'
if test.exists() and not first.exists():
    first.write_bytes(test.read_bytes())
for item in items:
    out = outdir/f"{item['name']}.png"
    if out.exists() and out.stat().st_size > 100_000:
        print('SKIP existing', out, flush=True)
        continue
    prompt = f"""{base_style}
Main title text: "{item['title']}".
Small subtitle text: "IA PARA NEGÓCIOS".
Concept: {item['concept']}.
Create a polished square icon/banner that can be used as a WhatsApp group photo, with the title large and readable, ZapClub-style neon green speech/data visual language, professional and cohesive with the provided ZapClub logo."""
    prompt_path = root/f"workspace/assets/prompts/{item['name']}.txt"
    prompt_path.write_text(prompt)
    cmd = ['uv','run','python',str(skill),'-o',str(out),'--provider','openai','-m','image2','-a','1:1','--prompt-file',str(prompt_path)]
    print('GENERATING', item['title'], flush=True)
    p = subprocess.run(cmd, cwd=str(root), text=True, capture_output=True, timeout=240)
    print(p.stdout[-2000:], flush=True)
    if p.returncode != 0:
        print(p.stderr[-4000:], file=sys.stderr, flush=True)
        sys.exit(p.returncode)
print('DONE', outdir, flush=True)
