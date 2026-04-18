
import React, { useState, useEffect } from 'react';
import { Brain, Play, Loader2, Clock, Wrench, Info, Zap } from 'lucide-react';
import { ReasoningTrace } from './ReasoningTrace';

export function ReasoningPanel({ reasoningEvents = [] }) {
    const [goal, setGoal] = useState('');
    const [running, setRunning] = useState(false);
    const [result, setResult] = useState(null);
    const [error, setError] = useState(null);
    const [info, setInfo] = useState(null);

    // Fetch reasoning agent info on mount
    useEffect(() => {
        fetch('api/reasoning/info')
            .then(res => {
                if (!res.ok) throw new Error(`Status ${res.status}`);
                return res.json();
            })
            .then(data => setInfo(data))
            .catch(err => console.error("Failed to fetch reasoning info:", err));
    }, []);

    const handleRun = async () => {
        if (!goal.trim() || running) return;

        setRunning(true);
        setResult(null);
        setError(null);

        try {
            const res = await fetch('api/reasoning/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ goal: goal.trim() }),
            });

            if (!res.ok) {
                const errText = await res.text();
                throw new Error(`API Error ${res.status}: ${errText}`);
            }

            const data = await res.json();
            setResult(data);
        } catch (e) {
            setError(e.message);
        } finally {
            setRunning(false);
        }
    };

    const handleKeyDown = (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleRun();
        }
    };

    return (
        <div className="space-y-6">
            {/* Header */}
            <div>
                <h2 className="text-lg font-semibold text-slate-100 flex items-center gap-2">
                    <Brain size={20} className="text-purple-400" /> Deep Reasoning
                </h2>
                <p className="text-slate-500 text-sm">Run multi-step reasoning goals with tool access</p>
            </div>

            {/* Info Bar */}
            {info && (
                <div className="flex items-center gap-4 px-4 py-3 bg-slate-900 border border-slate-800 rounded-xl text-sm">
                    <div className="flex items-center gap-2 text-slate-400">
                        <Info size={14} className="text-slate-500" />
                        <span className="font-medium text-slate-300">{info.name || info.agent_id || 'Reasoning Agent'}</span>
                    </div>
                    <div className="w-px h-4 bg-slate-800" />
                    <div className="flex items-center gap-1.5 text-slate-500">
                        <Zap size={12} />
                        <span className="font-mono text-xs">{info.backend || 'unknown'}</span>
                    </div>
                    <div className="w-px h-4 bg-slate-800" />
                    <div className="flex items-center gap-1.5 text-slate-500">
                        <Wrench size={12} />
                        <span className="font-mono text-xs">{info.tool_count ?? '?'} tools</span>
                    </div>
                    {info.external_mcp_connected && (
                        <>
                            <div className="w-px h-4 bg-slate-800" />
                            <span className="px-2 py-0.5 rounded text-[10px] font-bold tracking-wide uppercase bg-green-500/10 text-green-400 border border-green-500/20">
                                MCP Connected
                            </span>
                        </>
                    )}
                    <div className="ml-auto">
                        <span className={`px-2 py-0.5 rounded text-[10px] font-bold tracking-wide uppercase border
                            ${info.status === 'ready' || info.status === 'active'
                                ? 'bg-green-500/10 text-green-400 border-green-500/20'
                                : 'bg-slate-700/30 text-slate-400 border-slate-700'
                            }`}>
                            {info.status || 'unknown'}
                        </span>
                    </div>
                </div>
            )}

            {/* Goal Input */}
            <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                <div className="p-4 space-y-3">
                    <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wider">
                        Goal
                    </label>
                    <textarea
                        value={goal}
                        onChange={(e) => setGoal(e.target.value)}
                        onKeyDown={handleKeyDown}
                        placeholder="Describe the goal for the reasoning agent... e.g. 'Determine the optimal heating schedule for this week based on the weather forecast and occupancy patterns'"
                        rows={3}
                        className="w-full bg-slate-950 border border-slate-800 rounded-lg px-4 py-3 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-purple-500/50 focus:ring-1 focus:ring-purple-500/20 resize-none font-mono"
                        disabled={running}
                    />
                    <div className="flex items-center justify-between">
                        <span className="text-xs text-slate-600">Press Enter to run, Shift+Enter for newline</span>
                        <button
                            onClick={handleRun}
                            disabled={!goal.trim() || running}
                            className={`flex items-center gap-2 px-5 py-2 rounded-lg text-sm font-semibold transition-all duration-200
                                ${!goal.trim() || running
                                    ? 'bg-slate-800 text-slate-600 cursor-not-allowed'
                                    : 'bg-gradient-to-r from-purple-600 to-blue-600 text-white hover:from-purple-500 hover:to-blue-500 shadow-lg shadow-purple-900/20 hover:shadow-purple-900/40'
                                }`}
                        >
                            {running ? (
                                <>
                                    <Loader2 size={16} className="animate-spin" />
                                    Running...
                                </>
                            ) : (
                                <>
                                    <Play size={16} />
                                    Run
                                </>
                            )}
                        </button>
                    </div>
                </div>
            </div>

            {/* Error Display */}
            {error && (
                <div className="bg-red-500/5 border border-red-500/20 rounded-xl px-4 py-3 text-sm text-red-400">
                    <span className="font-semibold">Error:</span> {error}
                </div>
            )}

            {/* Result Summary */}
            {result && (
                <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                    <div className="px-4 py-3 bg-slate-900/80 border-b border-slate-800 flex items-center gap-2">
                        <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Result</span>
                        <span className={`ml-2 px-2 py-0.5 rounded text-[10px] font-bold tracking-wide uppercase border
                            ${result.stopped_reason === 'complete' || result.stopped_reason === 'solved'
                                ? 'bg-green-500/10 text-green-400 border-green-500/20'
                                : 'bg-amber-500/10 text-amber-400 border-amber-500/20'
                            }`}>
                            {result.stopped_reason || 'done'}
                        </span>
                    </div>

                    {/* Stats row */}
                    <div className="flex items-center gap-6 px-4 py-3 border-b border-slate-800/50 text-sm">
                        <div className="flex items-center gap-1.5 text-slate-400">
                            <Brain size={14} className="text-purple-400" />
                            <span className="font-mono text-xs">{result.iterations ?? '?'} iterations</span>
                        </div>
                        <div className="flex items-center gap-1.5 text-slate-400">
                            <Wrench size={14} className="text-blue-400" />
                            <span className="font-mono text-xs">{result.tool_calls ?? '?'} tool calls</span>
                        </div>
                        {result.duration_ms != null && (
                            <div className="flex items-center gap-1.5 text-slate-400">
                                <Clock size={14} className="text-slate-500" />
                                <span className="font-mono text-xs">
                                    {result.duration_ms >= 1000
                                        ? `${(result.duration_ms / 1000).toFixed(1)}s`
                                        : `${result.duration_ms}ms`
                                    }
                                </span>
                            </div>
                        )}
                    </div>

                    {/* Answer */}
                    <div className="p-4">
                        <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">Answer</h4>
                        <div className="text-sm text-slate-200 leading-relaxed whitespace-pre-wrap bg-slate-950 p-4 rounded-lg border border-slate-800">
                            {result.answer || 'No answer returned.'}
                        </div>
                    </div>
                </div>
            )}

            {/* Live Reasoning Trace */}
            {(reasoningEvents.length > 0 || (result && result.trace)) && (
                <ReasoningTrace reasoningEvents={reasoningEvents.length > 0 ? reasoningEvents : (result.trace || [])} />
            )}
        </div>
    );
}
