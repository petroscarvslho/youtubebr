"""
YouTube Narrator v6.1 - Server
Recebe legendas já extraídas e traduz para PT-BR
"""

import os
import re
import json
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

VERSION = "6.1"

# ============ TRADUÇÃO ============

def translate_with_groq(segments, api_key, source_language="en", context="", video_info=None):
    """Traduz segmentos usando Groq LLM"""
    
    if not segments:
        return [], ""
    
    # Gera contexto automático se não fornecido
    if not context and video_info:
        context = generate_context(video_info, segments[:5])
    
    # Agrupa segmentos em batches (~20 por vez)
    batch_size = 20
    translated_segments = []
    
    for i in range(0, len(segments), batch_size):
        batch = segments[i:i + batch_size]
        
        # Prepara textos
        texts = [f"[{s['index']}] {s['text']}" for s in batch]
        combined_text = "\n".join(texts)
        
        # Prompt
        system_prompt = f"""Você é um tradutor profissional especializado em tradução de {source_language} para português brasileiro (PT-BR).

CONTEXTO DO VÍDEO:
{context}

INSTRUÇÕES:
1. Traduza cada linha mantendo o número [N] no início
2. Use português brasileiro natural e fluente
3. Mantenha termos técnicos quando apropriado
4. Preserve nomes próprios
5. Adapte expressões idiomáticas para PT-BR
6. Mantenha o tom e estilo do original

Responda APENAS com as traduções, uma por linha, no formato:
[N] tradução em português"""

        try:
            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "llama-3.1-70b-versatile",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": combined_text}
                    ],
                    "temperature": 0.3,
                    "max_tokens": 4000
                },
                timeout=60
            )
            
            if response.status_code != 200:
                raise Exception(f"Groq API error: {response.status_code}")
            
            result = response.json()
            translated_text = result["choices"][0]["message"]["content"]
            
            # Parseia traduções
            translations = parse_translations(translated_text)
            
            # Associa traduções aos segmentos
            for seg in batch:
                idx = seg["index"]
                if idx in translations:
                    translated_segments.append({
                        **seg,
                        "translated": translations[idx]
                    })
                else:
                    # Fallback: usa original
                    translated_segments.append({
                        **seg,
                        "translated": seg["text"]
                    })
                    
        except Exception as e:
            print(f"Erro na tradução do batch: {e}")
            # Fallback: usa textos originais
            for seg in batch:
                translated_segments.append({
                    **seg,
                    "translated": seg["text"]
                })
    
    return translated_segments, context


def parse_translations(text):
    """Extrai traduções do texto retornado pelo LLM"""
    translations = {}
    
    # Regex para capturar [N] tradução
    pattern = r'\[(\d+)\]\s*(.+?)(?=\n\[|\Z)'
    matches = re.findall(pattern, text, re.DOTALL)
    
    for match in matches:
        idx = int(match[0])
        translation = match[1].strip()
        translations[idx] = translation
    
    return translations


def generate_context(video_info, sample_segments):
    """Gera contexto automático baseado no vídeo"""
    
    title = video_info.get("title", "")
    description = video_info.get("description", "")[:300]
    channel = video_info.get("channel", "")
    
    sample_text = " ".join([s.get("text", "") for s in sample_segments])[:500]
    
    context = f"Título: {title}\n"
    if channel:
        context += f"Canal: {channel}\n"
    if description:
        context += f"Descrição: {description}\n"
    
    return context.strip()


# ============ ENDPOINTS ============

@app.route("/")
def home():
    return jsonify({
        "service": "YouTube Narrator PT-BR",
        "version": VERSION,
        "status": "running"
    })


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "version": VERSION
    })


@app.route("/translate-segments", methods=["POST"])
def translate_segments():
    """
    Recebe legendas já extraídas e traduz para PT-BR
    
    Body:
    {
        "segments": [{"index": 0, "start": 0, "duration": 2, "text": "Hello"}],
        "source_language": "en",
        "api_key": "gsk_...",
        "context": "Optional custom context",
        "video_info": {"title": "...", "description": "...", "channel": "..."}
    }
    """
    try:
        data = request.get_json()
        
        segments = data.get("segments", [])
        source_language = data.get("source_language", "en")
        api_key = data.get("api_key", "")
        custom_context = data.get("context", "")
        video_info = data.get("video_info", {})
        
        if not segments:
            return jsonify({"error": "Nenhum segmento fornecido"}), 400
        
        if not api_key:
            return jsonify({"error": "API Key não fornecida"}), 400
        
        # Traduz
        translated, context_used = translate_with_groq(
            segments=segments,
            api_key=api_key,
            source_language=source_language,
            context=custom_context,
            video_info=video_info
        )
        
        return jsonify({
            "success": True,
            "segments": translated,
            "segment_count": len(translated),
            "source_language": source_language,
            "context_used": context_used
        })
        
    except Exception as e:
        print(f"Erro: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/translate", methods=["POST"])
def translate_legacy():
    """Endpoint legado - retorna erro informando nova arquitetura"""
    return jsonify({
        "error": "Este endpoint foi descontinuado. Use a versão 6.1 da extensão que extrai legendas localmente.",
        "version": VERSION
    }), 400


# ============ MAIN ============

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
