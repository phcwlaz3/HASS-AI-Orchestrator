
import React, { useState } from 'react';
import { ChevronDown, ChevronRight, Clock, Loader2, CheckCircle2, Wrench } from 'lucide-react';

export function ReasoningTrace({ reasoningEvents = [] }) {
    const [expandedSteps, setExpandedSteps] = useState({});
    const [expandedResults, setExpandedResults] = useState({});

    const toggleStep = (idx) => {
        setExpandedSteps(prev => ({ ...prev, [idx]: !prev[idx] }));
    };

    const toggleResult = (idx) => {
        setExpandedResults(prev => ({ ...prev, [idx]: !prev[idx] }));
    };

    if (reasoningEvents.length === 0) {
        return (
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-8 text-center text-slate-500 text-sm">
                No reasoning trace available. Run a goal to see the step-by-step trace.
            </div>
        );
    }

    return (
        <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
            <div className="px-4 py-3 bg-slate-900/80 border-b border-slate-800 flex items-center gap-2">
                <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Reasoning Trace</span>
                <span className="text-xs text-slate-600 font-mono">({reasoningEvents.length} steps)</span>
            </div>

            <div className="divide-y divide-slate-800/50">
                {reasoningEvents.map((evt, i) => {
                    const isExpanded = expandedSteps[i] ?? false;
                    const isResultExpanded = expandedResults[i] ?? false;
                    const isThinking = evt.status === 'thinking' || evt.status === 'running';
                    const toolCalls = evt.tool_calls || [];
                    const toolResults = evt.tool_results || evt.results || [];
                    const duration = evt.duration_ms != null ? evt.duration_ms : null;

                    return (
                        <div key={i} className="group">
                            {/* Step header row */}
                            <div
                                className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-slate-800/30 transition-colors"
                                onClick={() => toggleStep(i)}
                            >
                                <span className="text-slate-600 shrink-0">
                                    {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                                </span>

                                {/* Status indicator */}
                                <span className="shrink-0">
                                    {isThinking ? (
                                        <Loader2 size={14} className="text-amber-400 animate-spin" />
                                    ) : (
                                        <CheckCircle2 size={14} className="text-green-400" />
                                    )}
                                </span>

                                {/* Step label */}
                                <span className="text-sm font-medium text-slate-300">
                                    Iteration {i + 1}
                                </span>

                                {/* Tool call count badge */}
                                {toolCalls.length > 0 && (
                                    <span className="flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold tracking-wide uppercase bg-blue-500/10 text-blue-400 border border-blue-500/20">
                                        <Wrench size={10} />
                                        {toolCalls.length} tool{toolCalls.length !== 1 ? 's' : ''}
                                    </span>
                                )}

                                {/* Status badge */}
                                <span className={`px-2 py-0.5 rounded text-[10px] font-bold tracking-wide uppercase border
                                    ${isThinking
                                        ? 'bg-amber-500/10 text-amber-400 border-amber-500/20'
                                        : 'bg-green-500/10 text-green-400 border-green-500/20'
                                    }`}>
                                    {isThinking ? 'Thinking' : 'Complete'}
                                </span>

                                {/* Duration */}
                                {duration != null && (
                                    <span className="ml-auto flex items-center gap-1 text-xs text-slate-500 font-mono">
                                        <Clock size={10} />
                                        {duration >= 1000 ? `${(duration / 1000).toFixed(1)}s` : `${duration}ms`}
                                    </span>
                                )}
                            </div>

                            {/* Expanded step details */}
                            {isExpanded && (
                                <div className="px-10 pb-4 space-y-3">
                                    {/* Thought */}
                                    {evt.thought && (
                                        <div>
                                            <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">Thought</h4>
                                            <p className="text-sm text-slate-300 leading-relaxed whitespace-pre-wrap bg-slate-950 p-3 rounded-lg border border-slate-800">
                                                {evt.thought}
                                            </p>
                                        </div>
                                    )}

                                    {/* Tool Calls */}
                                    {toolCalls.length > 0 && (
                                        <div>
                                            <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">Tool Calls</h4>
                                            <div className="space-y-2">
                                                {toolCalls.map((tc, j) => (
                                                    <div key={j} className="bg-slate-950 border border-slate-800 rounded-lg overflow-hidden">
                                                        <div className="px-3 py-2 flex items-center gap-2 border-b border-slate-800/50">
                                                            <Wrench size={12} className="text-blue-400" />
                                                            <span className="text-xs font-bold text-blue-400 font-mono">{tc.name || tc.tool || 'unknown'}</span>
                                                        </div>
                                                        <pre className="text-xs font-mono text-slate-400 whitespace-pre-wrap break-all p-3 overflow-x-auto">
                                                            {typeof tc.args === 'string' ? tc.args : JSON.stringify(tc.args || tc.arguments || {}, null, 2)}
                                                        </pre>
                                                    </div>
                                                ))}
                                            </div>
                                        </div>
                                    )}

                                    {/* Tool Results (collapsible) */}
                                    {toolResults.length > 0 && (
                                        <div>
                                            <button
                                                onClick={(e) => { e.stopPropagation(); toggleResult(i); }}
                                                className="flex items-center gap-1 text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1 hover:text-slate-400 transition-colors"
                                            >
                                                {isResultExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                                                Tool Results ({toolResults.length})
                                            </button>
                                            {isResultExpanded && (
                                                <div className="space-y-2">
                                                    {toolResults.map((tr, k) => (
                                                        <pre key={k} className="text-xs font-mono text-slate-400 whitespace-pre-wrap break-all bg-slate-950 p-3 rounded-lg border border-slate-800 overflow-x-auto">
                                                            {typeof tr === 'string' ? tr : JSON.stringify(tr, null, 2)}
                                                        </pre>
                                                    ))}
                                                </div>
                                            )}
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>
                    );
                })}
            </div>
        </div>
    );
}
