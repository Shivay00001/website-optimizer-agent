'use client';

import { useState, useEffect } from 'react';

export default function Home() {
  const [provider, setProvider] = useState('gpt-4o');
  const [keys, setKeys] = useState({
    openai: '',
    anthropic: '',
    gemini: '',
    glm: ''
  });
  
  const [targetUrl, setTargetUrl] = useState('');
  
  const [status, setStatus] = useState<'idle' | 'pending' | 'running' | 'success' | 'error'>('idle');
  const [taskId, setTaskId] = useState('');
  const [reportData, setReportData] = useState<any>(null);
  const [message, setMessage] = useState('');

  useEffect(() => {
    setKeys({
      openai: localStorage.getItem('opt_openai_key') || '',
      anthropic: localStorage.getItem('opt_anthropic_key') || '',
      gemini: localStorage.getItem('opt_gemini_key') || '',
      glm: localStorage.getItem('opt_glm_key') || ''
    });

    let interval: NodeJS.Timeout;
    if (taskId && (status === 'pending' || status === 'running')) {
      interval = setInterval(async () => {
        try {
          const res = await fetch(`http://localhost:8010/api/tasks/${taskId}`);
          if (res.ok) {
            const data = await res.json();
            setStatus(data.status);
            if (data.status === 'success') {
              setReportData(data.report);
              setMessage('Audit complete!');
            } else if (data.status === 'error') {
              setMessage('Error performing audit.');
            }
          }
        } catch (e) {
          console.error("Polling error", e);
        }
      }, 3000);
    }
    return () => clearInterval(interval);
  }, [taskId, status]);

  const handleKeyChange = (provider: string, val: string) => {
    setKeys(prev => ({...prev, [provider]: val}));
  };

  const handleExecute = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!targetUrl) return;

    setStatus('pending');
    setMessage(`Running headless scrape and feeding DOM to ${provider}...`);
    setReportData(null);
    
    try {
      localStorage.setItem('opt_openai_key', keys.openai);
      localStorage.setItem('opt_anthropic_key', keys.anthropic);
      localStorage.setItem('opt_gemini_key', keys.gemini);
      localStorage.setItem('opt_glm_key', keys.glm);

      const res = await fetch('http://localhost:8010/api/execute', {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'X-OpenAI-Key': keys.openai,
          'X-Anthropic-Key': keys.anthropic,
          'X-Gemini-Key': keys.gemini,
          'X-GLM-Key': keys.glm,
        },
        body: JSON.stringify({
          target_url: targetUrl,
          model_id: provider
        }),
      });
      
      const data = await res.json();
      if (res.ok) {
        setTaskId(data.task_id);
      } else {
        setStatus('error');
        setMessage('Failed to start task.');
      }
    } catch (e) {
      console.error(e);
      setStatus('error');
      setMessage('Network error.');
    }
  };

  const getScoreClass = (score: number) => {
    if (score >= 90) return 'good';
    if (score >= 50) return 'average';
    return 'poor';
  };

  return (
    <main className="dashboard-container">
      <div className="dashboard-header">
        <h1>Website Optimizer AI</h1>
        <p>Technical SEO & CRO Auditor Powered by Multi-LLM Routing</p>
      </div>

      <div style={{display: 'flex', gap: '30px', flexWrap: 'wrap'}}>
        <div style={{flex: '1 1 400px'}}>
          <div className="panel">
            <h2 className="panel-title">Universal API Gateway</h2>
            
            <div className="form-group">
              <label>OpenAI API Key (GPT-4o)</label>
              <input type="password" value={keys.openai} onChange={(e) => handleKeyChange('openai', e.target.value)} />
            </div>
            <div className="form-group">
              <label>Anthropic API Key (Claude 3.5)</label>
              <input type="password" value={keys.anthropic} onChange={(e) => handleKeyChange('anthropic', e.target.value)} />
            </div>
            <div className="form-group">
              <label>Google AI Key (Gemini 1.5)</label>
              <input type="password" value={keys.gemini} onChange={(e) => handleKeyChange('gemini', e.target.value)} />
            </div>
            <div className="form-group">
              <label>ZhipuAI Key (GLM-4)</label>
              <input type="password" value={keys.glm} onChange={(e) => handleKeyChange('glm', e.target.value)} />
            </div>

            <div className="form-group" style={{marginTop: '30px'}}>
              <label style={{color: 'var(--primary)', fontWeight: 'bold'}}>Active LLM Router (litellm)</label>
              <select value={provider} onChange={(e) => setProvider(e.target.value)} disabled={status === 'pending' || status === 'running'} style={{border: '2px solid var(--primary)'}}>
                <option value="gpt-4o">OpenAI (gpt-4o)</option>
                <option value="claude-3-5-sonnet-20240620">Anthropic (claude-3-5-sonnet)</option>
                <option value="gemini/gemini-1.5-pro">Google AI (gemini-1.5-pro)</option>
                <option value="zhipu/glm-4">ZhipuAI (glm-4)</option>
                <option value="ollama/llama3">Ollama (llama3 - local)</option>
              </select>
            </div>
          </div>

          <div className="panel">
            <h2 className="panel-title">Audit Target</h2>
            <form onSubmit={handleExecute}>
              <div className="form-group">
                <label>Website URL</label>
                <input 
                  type="url" 
                  value={targetUrl} 
                  onChange={(e) => setTargetUrl(e.target.value)} 
                  placeholder="https://example.com"
                  required
                  disabled={status === 'pending' || status === 'running'}
                />
              </div>
              <button type="submit" className="btn" style={{width: '100%'}} disabled={status === 'pending' || status === 'running'}>
                {status === 'pending' || status === 'running' ? 'Scanning DOM...' : 'Run Technical Audit'}
              </button>
            </form>
          </div>

          {status !== 'idle' && status !== 'success' && (
            <div className={`status-message ${status}`}>
              {message}
            </div>
          )}
        </div>

        <div style={{flex: '2 1 600px'}}>
          <div className="panel" style={{height: '100%'}}>
            <h2 className="panel-title">Audit Report</h2>
            
            {status === 'idle' ? (
              <p style={{color: '#9ca3af', textAlign: 'center', marginTop: '60px'}}>Enter a URL to generate an audit.</p>
            ) : status === 'running' || status === 'pending' ? (
              <div style={{textAlign: 'center', marginTop: '60px'}}>
                <div style={{width: '50px', height: '50px', border: '4px solid #e5e7eb', borderTopColor: 'var(--primary)', borderRadius: '50%', animation: 'spin 1s linear infinite', margin: '0 auto'}}></div>
                <style>{`@keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }`}</style>
                <p style={{marginTop: '20px', fontWeight: 'bold', color: 'var(--primary)'}}>Analyzing with {provider}...</p>
              </div>
            ) : reportData ? (
              <div>
                <div style={{display: 'flex', justifyContent: 'space-around', marginBottom: '40px'}}>
                  <div style={{textAlign: 'center'}}>
                    <div className={`score-circle ${getScoreClass(reportData.seo_score)}`}>{reportData.seo_score}</div>
                    <div style={{fontWeight: 'bold', marginTop: '10px'}}>SEO Score</div>
                  </div>
                  <div style={{textAlign: 'center'}}>
                    <div className={`score-circle ${getScoreClass(reportData.cro_score)}`}>{reportData.cro_score}</div>
                    <div style={{fontWeight: 'bold', marginTop: '10px'}}>CRO Score</div>
                  </div>
                </div>

                <div style={{marginBottom: '30px'}}>
                  <h3 style={{color: 'var(--error)', fontWeight: 'bold', marginBottom: '10px'}}>Critical Issues</h3>
                  <div style={{border: '1px solid var(--border)', borderRadius: '8px'}}>
                    {reportData.critical_issues?.map((item: string, i: number) => (
                      <div key={i} className="list-item">❌ {item}</div>
                    ))}
                  </div>
                </div>

                <div style={{display: 'flex', gap: '20px'}}>
                  <div style={{flex: 1}}>
                    <h3 style={{color: 'var(--primary)', fontWeight: 'bold', marginBottom: '10px'}}>SEO Fixes</h3>
                    <div style={{border: '1px solid var(--border)', borderRadius: '8px'}}>
                      {reportData.seo_recommendations?.map((item: string, i: number) => (
                        <div key={i} className="list-item" style={{fontSize: '0.9rem'}}>🔍 {item}</div>
                      ))}
                    </div>
                  </div>
                  <div style={{flex: 1}}>
                    <h3 style={{color: 'var(--success)', fontWeight: 'bold', marginBottom: '10px'}}>CRO Boosts</h3>
                    <div style={{border: '1px solid var(--border)', borderRadius: '8px'}}>
                      {reportData.cro_recommendations?.map((item: string, i: number) => (
                        <div key={i} className="list-item" style={{fontSize: '0.9rem'}}>📈 {item}</div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            ) : (
              <p>Failed to parse report.</p>
            )}
          </div>
        </div>
      </div>
    </main>
  );
}
