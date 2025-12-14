"""
YouTube Narrator v5.1 - Server Python (Render)
Extrai legendas ASR e traduz com Groq LLM
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound
import requests
import re
import os

app = Flask(__name__)
CORS(app)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"


def extract_video_id(url_or_id: str) -> str:
    patterns = [
        r'(?:v=|/v/|youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'^([a-zA-Z0-9_-]{11})$'
    ]
    for pattern in patterns:
        match = re.search(pattern, url_or_id)
        if match:
            return match.group(1)
    return url_or_id


def get_video_metadata(video_id: str) -> dict:
    try:
        url = f"https://www.youtube.com/watch?v={video_id}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept-Language': 'en-US,en;q=0.9'
        }
        response = requests.get(url, headers=headers, timeout=10)
        html = response.text
        
        title_match = re.search(r'"title":"([^"]+)"', html)
        title = title_match.group(1) if title_match else ""
        
        desc_match = re.search(r'"shortDescription":"([^"]{0,500})', html)
        description = desc_match.group(1) if desc_match else ""
        description = description.replace('\\n', ' ').replace('\\', '')
        
        channel_match = re.search(r'"ownerChannelName":"([^"]+)"', html)
        channel = channel_match.group(1) if channel_match else ""
        
        return {"title": title, "description": description[:500], "channel": channel}
    except Exception as e:
        print(f"[Server] Erro metadata: {e}")
        return {"title": "", "description": "", "channel": ""}


def generate_context_from_content(metadata: dict, first_segments: list, api_key: str) -> str:
    first_texts = " ".join([seg['text'] for seg in first_segments[:20]])
    
    prompt = f"""Analise este video e gere um BREVE contexto (2-3 frases) para ajudar na traducao EN->PT-BR.

Titulo: {metadata.get('title', 'N/A')}
Canal: {metadata.get('channel', 'N/A')}
Descricao: {metadata.get('description', 'N/A')}

Primeiras falas:
{first_texts}

Responda APENAS com:
1. Uma frase descrevendo o tema/assunto do video
2. Termos tecnicos importantes (se houver)

Exemplo: "Video sobre programacao Python. Termos: function=funcao, loop=laco."

Sua resposta:"""

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 300
    }
    
    try:
        response = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=30)
        if response.status_code == 200:
            return response.json()['choices'][0]['message']['content'].strip()
    except Exception as e:
        print(f"[Server] Erro contexto: {e}")
    
    return f"Video: {metadata.get('title', 'Conteudo geral')}"


def get_transcript(video_id: str) -> tuple:
    ytt = YouTubeTranscriptApi()
    
    try:
        transcript_list = ytt.list(video_id)
        preferred_langs = ['en', 'en-US', 'en-GB']
        
        try:
            transcript = transcript_list.find_transcript(preferred_langs)
            captions = transcript.fetch()
            return (captions, transcript.language_code, transcript.is_generated)
        except:
            pass
        
        for transcript in transcript_list:
            captions = transcript.fetch()
            return (captions, transcript.language_code, transcript.is_generated)
        
        raise Exception("Nenhuma legenda encontrada")
        
    except TranscriptsDisabled:
        raise Exception("Legendas desabilitadas neste video")
    except NoTranscriptFound:
        raise Exception("Nenhuma legenda encontrada")
    except Exception as e:
        raise Exception(f"Erro ao extrair legendas: {str(e)}")


def group_segments(transcript: list, max_duration: float = 8.0) -> list:
    grouped = []
    current = {'text': '', 'start': 0, 'duration': 0}
    
    for seg in transcript:
        text = seg.get('text', '')
        start = seg.get('start', 0)
        duration = seg.get('duration', 2.0)
        
        if not current['text']:
            current = {'text': text, 'start': start, 'duration': duration}
        elif (start - current['start']) < max_duration:
            current['text'] += ' ' + text
            current['duration'] = (start + duration) - current['start']
        else:
            grouped.append(current)
            current = {'text': text, 'start': start, 'duration': duration}
    
    if current['text']:
        grouped.append(current)
    
    return grouped


def translate_batch(segments: list, api_key: str, context: str = "") -> list:
    texts = [f"[{i}] {seg['text']}" for i, seg in enumerate(segments)]
    all_texts = "\n".join(texts)
    
    system_prompt = """Voce e um tradutor profissional EN->PT-BR especializado em dublagem.

REGRAS:
1. Traduza de forma NATURAL e CONCISA
2. Mantenha termos tecnicos quando apropriado
3. Responda APENAS: [numero] traducao
4. NAO adicione explicacoes
5. Mantenha numeracao [0], [1], [2]..."""

    user_prompt = f"Traduza para PT-BR:\n\n{all_texts}"
    if context:
        user_prompt = f"Contexto: {context}\n\n{user_prompt}"

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.3,
        "max_tokens": 8000
    }
    
    response = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=60)
    
    if response.status_code != 200:
        raise Exception(f"Erro Groq: {response.status_code}")
    
    translated_text = response.json()['choices'][0]['message']['content']
    
    translations = {}
    for line in translated_text.strip().split('\n'):
        match = re.match(r'\[(\d+)\]\s*(.+)', line.strip())
        if match:
            translations[int(match.group(1))] = match.group(2).strip()
    
    result = []
    for i, seg in enumerate(segments):
        result.append({
            'start': seg['start'],
            'duration': seg['duration'],
            'original': seg['text'],
            'translated': translations.get(i, seg['text'])
        })
    
    return result


def estimate_speech_rate(text: str, available_duration: float) -> float:
    words = len(text.split())
    estimated = words / 2.5
    if estimated <= available_duration:
        return 1.0
    return min(estimated / available_duration, 1.5)


@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "name": "YouTube Narrator PT-BR",
        "version": "5.1",
        "status": "online",
        "endpoints": ["/health", "/translate", "/extract", "/languages"]
    })


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "version": "5.1"})


@app.route('/extract', methods=['POST'])
def extract():
    data = request.json
    video_url = data.get('video_url') or data.get('video_id')
    
    if not video_url:
        return jsonify({"error": "video_url obrigatorio"}), 400
    
    try:
        video_id = extract_video_id(video_url)
        transcript, lang, is_generated = get_transcript(video_id)
        grouped = group_segments(transcript)
        
        return jsonify({
            "success": True,
            "video_id": video_id,
            "language": lang,
            "is_generated": is_generated,
            "segment_count": len(grouped),
            "segments": grouped
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/translate', methods=['POST'])
def translate():
    data = request.json
    video_url = data.get('video_url') or data.get('video_id')
    api_key = data.get('api_key')
    context = data.get('context', '')
    auto_context = data.get('auto_context', True)
    
    if not video_url:
        return jsonify({"error": "video_url obrigatorio"}), 400
    if not api_key:
        return jsonify({"error": "api_key obrigatorio"}), 400
    
    try:
        video_id = extract_video_id(video_url)
        print(f"[Server] Extraindo: {video_id}")
        
        transcript, lang, is_generated = get_transcript(video_id)
        grouped = group_segments(transcript)
        print(f"[Server] {len(grouped)} segmentos ({lang})")
        
        generated_context = ""
        if not context and auto_context:
            print("[Server] Gerando contexto...")
            metadata = get_video_metadata(video_id)
            generated_context = generate_context_from_content(metadata, transcript[:30], api_key)
            context = generated_context
        
        batch_size = 50
        all_translated = []
        
        for i in range(0, len(grouped), batch_size):
            batch = grouped[i:i + batch_size]
            print(f"[Server] Traduzindo batch {i//batch_size + 1}...")
            translated = translate_batch(batch, api_key, context)
            all_translated.extend(translated)
        
        for seg in all_translated:
            seg['rate'] = estimate_speech_rate(seg['translated'], seg['duration'])
        
        print(f"[Server] Pronto! {len(all_translated)} segmentos")
        
        return jsonify({
            "success": True,
            "video_id": video_id,
            "source_language": lang,
            "is_generated": is_generated,
            "segment_count": len(all_translated),
            "context_used": context,
            "context_auto_generated": bool(generated_context),
            "segments": all_translated
        })
    except Exception as e:
        print(f"[Server] Erro: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/languages', methods=['POST'])
def list_languages():
    data = request.json
    video_url = data.get('video_url') or data.get('video_id')
    
    if not video_url:
        return jsonify({"error": "video_url obrigatorio"}), 400
    
    try:
        video_id = extract_video_id(video_url)
        ytt = YouTubeTranscriptApi()
        transcript_list = ytt.list(video_id)
        
        languages = []
        for t in transcript_list:
            languages.append({
                "code": t.language_code,
                "name": t.language,
                "is_generated": t.is_generated
            })
        
        return jsonify({"success": True, "video_id": video_id, "languages": languages})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"Server rodando na porta {port}")
    app.run(host='0.0.0.0', port=port)
