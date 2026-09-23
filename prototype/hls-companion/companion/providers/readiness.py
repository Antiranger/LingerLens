"""Release readiness is independent of language capability and saved labels.

Candidate means suitable for targeted live acceptance, NOT certified performance.
Known-incompatible modes fail once before acquisition instead of retrying forever.
"""
from .base import ProviderRequestError

def asr_readiness(config):
    kind, model = config.get('kind'), config.get('model')
    options = config.get('options') or {}
    native = kind == 'dashscope-livetranslate-realtime' or (
        kind == 'soniox-realtime' and options.get('translationType') in {'one_way','two_way'})
    if kind == 'openai-realtime-transcription' and model == 'gpt-live-transcribe':
        return {'tier':'blocked','reason':'client_vad_not_implemented','nativeTranslation':False}
    if kind == 'elevenlabs-scribe-realtime' and options.get('commitStrategy','vad') == 'manual':
        return {'tier':'blocked','reason':'manual_commit_not_scheduled','nativeTranslation':False}
    candidate = (kind in {'soniox-realtime','soniox-realtime-transcribe'} and model == 'stt-rt-v5' and not native) or (kind == 'dashscope-qwen-realtime' and model == 'qwen3-asr-flash-realtime')
    return {'tier':'candidate' if candidate else 'experimental',
            'reason':'live_acceptance_required' if not native else 'native_segment_alignment_required',
            'nativeTranslation':native}

def validate_asr_start(config):
    state=asr_readiness(config)
    if state['tier']=='blocked':
        raise ProviderRequestError('ASR profile cannot start: '+state['reason']+
            '. Select a server-VAD profile; this profile remains editable.')
    return state
