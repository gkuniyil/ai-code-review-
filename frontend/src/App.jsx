import { useState, useEffect } from "react";

export default function App() {
  const [metrics, setMetrics] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // fetch metrics from your FastAPI backend
    fetch("/metrics")
      .then(res => res.json())
      .then(data => {
        setMetrics(data);
        setLoading(false);
      });
  }, []);

  if (loading) return <div className="p-8">Loading metrics...</div>;

  return (
    <div className="p-8 bg-gray-50 min-h-screen">
      <h1 className="text-3xl font-bold mb-8">🤖 AI Code Reviewer Dashboard</h1>

      {/* summary cards */}
      <div className="grid grid-cols-3 gap-6 mb-8">
        <div className="bg-white rounded-lg p-6 shadow">
          <p className="text-gray-500">PRs Reviewed</p>
          <p className="text-4xl font-bold">{metrics.total_prs_reviewed}</p>
        </div>
        <div className="bg-white rounded-lg p-6 shadow">
          <p className="text-gray-500">Issues Found</p>
          <p className="text-4xl font-bold">{metrics.total_findings}</p>
        </div>
        <div className="bg-white rounded-lg p-6 shadow">
          <p className="text-gray-500">Avg Review Time</p>
          <p className="text-4xl font-bold">{metrics.avg_review_time_seconds}s</p>
        </div>
      </div>

      {/* severity breakdown */}
      <div className="bg-white rounded-lg p-6 shadow mb-8">
        <h2 className="text-xl font-bold mb-4">Issues By Severity</h2>
        <div className="flex gap-8">
          <div className="text-center">
            <p className="text-red-500 text-3xl font-bold">
              {metrics.findings_by_severity?.high || 0}
            </p>
            <p className="text-gray-500">High</p>
          </div>
          <div className="text-center">
            <p className="text-yellow-500 text-3xl font-bold">
              {metrics.findings_by_severity?.medium || 0}
            </p>
            <p className="text-gray-500">Medium</p>
          </div>
          <div className="text-center">
            <p className="text-green-500 text-3xl font-bold">
              {metrics.findings_by_severity?.low || 0}
            </p>
            <p className="text-gray-500">Low</p>
          </div>
        </div>
      </div>

      {/* agent breakdown */}
      <div className="bg-white rounded-lg p-6 shadow">
        <h2 className="text-xl font-bold mb-4">Issues By Agent</h2>
        {Object.entries(metrics.findings_by_agent || {}).map(([agent, count]) => (
          <div key={agent} className="flex justify-between py-2 border-b">
            <span className="capitalize">{agent}</span>
            <span className="font-bold">{count}</span>
          </div>
        ))}
      </div>
    </div>
  );
}