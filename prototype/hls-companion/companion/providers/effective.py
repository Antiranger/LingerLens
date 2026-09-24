"""Credential-free saved configuration summary; not a claim about a running session."""
from .readiness import asr_readiness

def effective_settings(config, path=None):
    def active(section):
        group=config.get(section,{})
        return next((p for p in group.get('providers',[]) if p.get('id')==group.get('active')), {})
    def identity(p):
        return {key:p.get(key) for key in ('id','kind','model')}
    asr=active('asr'); mt=active('translation')
    readiness=asr_readiness(asr)
    native=readiness['nativeTranslation']
    fallback=native and (asr.get('options') or {}).get('nativeTranslationFallback') is True
    options=asr.get('options') or {}
    timing={}
    for key in ('sampleRate','silenceDurationMs','maxSentenceSilence','maxEndpointDelayMs',
                'vadSilenceThresholdSecs','nativeTranslationTimeoutSeconds'):
        value=options.get(key)
        if isinstance(value,(int,float)) and not isinstance(value,bool): timing[key]=value
    turn=options.get('turnDetection')
    if isinstance(turn,dict):
        timing['turnDetection']={k:turn[k] for k in ('type','threshold','silenceDurationMs') if k in turn}
    subtitle=config.get('subtitle',{})
    return {'scope':'saved-next-start','configurationPath':str(path) if path else None,
            'asr':identity(asr),'readiness':readiness,'asrTiming':timing,
            'subtitleTranslation':{'mode':('native-with-explicit-fallback' if fallback else 'native-final-segment') if native else 'separate-model',
                                   'fallbackProvider':identity(mt) if fallback else None,
                                   'provider':identity(asr if native else mt),
                                   'timeoutSeconds':options.get('nativeTranslationTimeoutSeconds',15) if native else (mt.get('options') or {}).get('timeoutSeconds',6)},
            'chatTranslation':{'active':config.get('chatTranslation',{}).get('active')},
            'languages':{k:subtitle.get(k) for k in ('sourceLanguage','targetLanguage')},
            'translationWorkers':subtitle.get('translationWorkers',4)}
