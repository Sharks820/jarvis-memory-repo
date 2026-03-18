// Copyright (c) 2026 Conner McCarthy. All rights reserved.
// Unity Editor Bridge — JarvisPanel EditorWindow.
//
// Shows the agent's current step, status, recent log entries, and an
// approve / reject UI for approval-gated operations (UNITY-05).
//
// Menu: Window > Jarvis > Agent Panel
//
// Integration notes:
//   - Subscribes to JarvisEditorBridge.OnAgentMessage (raised on main thread).
//   - State is non-static so it survives domain reload via OnEnable re-registration.
//   - Log entries are ephemeral — intentionally cleared on domain reload.
//   - Approve / Reject send a JSON-RPC response back to the Python agent via
//     JarvisEditorBridge.BroadcastRaw().

using System;
using System.Collections.Generic;
using UnityEditor;
using UnityEngine;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Jarvis.EditorBridge.Models;

namespace Jarvis.EditorBridge
{
    /// <summary>
    /// EditorWindow that mirrors the Python agent's real-time progress inside the
    /// Unity Editor.  Open via <b>Window &gt; Jarvis &gt; Agent Panel</b>.
    /// </summary>
    public class JarvisPanel : EditorWindow
    {
        // ── Constants ──────────────────────────────────────────────────────────

        private const int MaxLogEntries = 50;

        // ── State ──────────────────────────────────────────────────────────────

        private string _currentStep   = "Idle";
        private string _currentStatus = "idle";          // idle / running / waiting_approval / done / failed

        private readonly List<string> _logEntries = new List<string>();
        private Vector2 _logScrollPos;

        private bool   _approvalPending     = false;
        private string _approvalTaskId      = "";
        private string _approvalDescription = "";

        // Cached GUIStyles to avoid per-frame allocation
        private GUIStyle _statusStyle;
        private GUIStyle _pendingStyle;

        // ── Menu registration ──────────────────────────────────────────────────

        /// <summary>Open or focus the Jarvis Agent Panel.</summary>
        [MenuItem("Window/Jarvis/Agent Panel")]
        public static void ShowWindow()
        {
            GetWindow<JarvisPanel>("Jarvis Agent");
        }

        // ── EditorWindow lifecycle ─────────────────────────────────────────────

        private void OnEnable()
        {
            JarvisEditorBridge.OnAgentMessage += HandleAgentMessage;
        }

        private void OnDisable()
        {
            JarvisEditorBridge.OnAgentMessage -= HandleAgentMessage;
        }

        // ── Message handling ───────────────────────────────────────────────────

        private void HandleAgentMessage(string jsonMessage)
        {
            try
            {
                var obj = JObject.Parse(jsonMessage);
                var method = obj["method"]?.Value<string>() ?? string.Empty;
                var p      = obj["params"] as JObject;

                switch (method)
                {
                    case "agent_step_start":
                        _currentStep   = p?["step"]?.Value<string>() ?? _currentStep;
                        _currentStatus = "running";
                        AddLog($"Step started: {_currentStep}");
                        break;

                    case "agent_step_done":
                        _currentStatus = p?["status"]?.Value<string>() ?? "done";
                        var stepDoneMsg = p?["message"]?.Value<string>() ?? $"Step done: {_currentStep}";
                        AddLog(stepDoneMsg);
                        break;

                    case "agent_task_done":
                        _currentStatus = "done";
                        var taskDoneMsg = p?["message"]?.Value<string>() ?? "Task completed";
                        AddLog(taskDoneMsg);
                        break;

                    case "agent_task_failed":
                        _currentStatus = "failed";
                        var errorMsg = p?["error"]?.Value<string>() ?? "Task failed";
                        AddLog($"ERROR: {errorMsg}");
                        break;

                    case "agent_approval_needed":
                        _approvalPending     = true;
                        _approvalTaskId      = p?["task_id"]?.Value<string>()     ?? "";
                        _approvalDescription = p?["description"]?.Value<string>() ?? "(no description)";
                        _currentStatus       = "waiting_approval";
                        AddLog($"Approval needed: {_approvalDescription}");
                        break;
                }
            }
            catch (Exception ex)
            {
                // JSON parse failures should not crash the Editor
                Debug.LogWarning($"[Jarvis] JarvisPanel could not parse agent message: {ex.Message}");
            }

            Repaint();
        }

        // ── OnGUI ──────────────────────────────────────────────────────────────

        private void OnGUI()
        {
            DrawStatusSection();
            GUILayout.Space(4);
            DrawLogSection();
            GUILayout.Space(4);
            DrawApprovalSection();
        }

        // ── GUI sections ───────────────────────────────────────────────────────

        private void DrawStatusSection()
        {
            EditorGUILayout.BeginVertical(EditorStyles.helpBox);

            EditorGUILayout.BeginHorizontal();
            EditorGUILayout.LabelField("Status:", GUILayout.Width(50));

            if (_statusStyle == null)
                _statusStyle = new GUIStyle(EditorStyles.boldLabel);
            _statusStyle.normal.textColor = StatusColor(_currentStatus);
            EditorGUILayout.LabelField(_currentStatus, _statusStyle, GUILayout.ExpandWidth(true));
            EditorGUILayout.EndHorizontal();

            EditorGUILayout.BeginHorizontal();
            EditorGUILayout.LabelField("Step:", GUILayout.Width(50));
            EditorGUILayout.LabelField(_currentStep, EditorStyles.boldLabel);
            EditorGUILayout.EndHorizontal();

            EditorGUILayout.EndVertical();
        }

        private void DrawLogSection()
        {
            EditorGUILayout.LabelField("Log", EditorStyles.boldLabel);

            // Reserve remaining vertical space minus approval panel height (≈ 70 px)
            float logHeight = position.height
                              - EditorGUIUtility.singleLineHeight * 3f  // status section
                              - (_approvalPending ? 70f : 0f)
                              - 60f;  // labels + spacing
            logHeight = Mathf.Max(logHeight, 60f);

            _logScrollPos = EditorGUILayout.BeginScrollView(
                _logScrollPos,
                EditorStyles.helpBox,
                GUILayout.Height(logHeight));

            for (int i = 0; i < _logEntries.Count; i++)
                EditorGUILayout.LabelField(_logEntries[i], EditorStyles.miniLabel);

            EditorGUILayout.EndScrollView();
        }

        private void DrawApprovalSection()
        {
            if (!_approvalPending) return;

            EditorGUILayout.BeginVertical(EditorStyles.helpBox);

            if (_pendingStyle == null)
            {
                _pendingStyle = new GUIStyle(EditorStyles.wordWrappedLabel);
                _pendingStyle.normal.textColor = new Color(1f, 0.85f, 0f);  // yellow
            }
            EditorGUILayout.LabelField($"Approval: \"{_approvalDescription}\"", _pendingStyle);

            EditorGUILayout.BeginHorizontal();

            if (GUILayout.Button("Approve", GUILayout.Height(28)))
                SendApprovalResponse(approved: true);

            if (GUILayout.Button("Reject", GUILayout.Height(28)))
                SendApprovalResponse(approved: false);

            EditorGUILayout.EndHorizontal();
            EditorGUILayout.EndVertical();
        }

        // ── Approve / Reject ───────────────────────────────────────────────────

        private void SendApprovalResponse(bool approved)
        {
            try
            {
                var response = JsonRpcResponse.Success(
                    _approvalTaskId,
                    new { approved = approved });

                var payload = JsonConvert.SerializeObject(response);
                JarvisEditorBridge.BroadcastRaw(payload);

                AddLog(approved
                    ? $"Approved: {_approvalDescription}"
                    : $"Rejected: {_approvalDescription}");
            }
            catch (Exception ex)
            {
                Debug.LogError($"[Jarvis] SendApprovalResponse failed: {ex.Message}");
            }
            finally
            {
                // Always clear the approval gate, even on failure
                _approvalPending     = false;
                _approvalTaskId      = "";
                _approvalDescription = "";
                _currentStatus       = "idle";
                Repaint();
            }
        }

        // ── Helpers ────────────────────────────────────────────────────────────

        private void AddLog(string message)
        {
            var timestamp = DateTime.Now.ToString("HH:mm:ss");
            _logEntries.Add($"[{timestamp}] {message}");

            // FIFO cap — drop the oldest entry when over the limit
            while (_logEntries.Count > MaxLogEntries)
                _logEntries.RemoveAt(0);
        }

        private static Color StatusColor(string status)
        {
            switch (status)
            {
                case "running":          return new Color(0.2f, 0.85f, 0.2f);   // green
                case "waiting_approval": return new Color(1f,   0.85f, 0f);     // yellow
                case "failed":           return new Color(0.9f, 0.2f, 0.2f);    // red
                default:                 return new Color(0.7f, 0.7f, 0.7f);    // gray (idle / done)
            }
        }
    }
}
