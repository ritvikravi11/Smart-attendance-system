/**
 * Typed wrappers around every backend endpoint the UI uses.
 *
 * One function per endpoint, grouped by resource. Components import from here
 * and never construct a URL themselves -- so a route change on the server
 * means editing one line, not grepping for a string.
 */

import { api } from "./client";

export const authApi = {
  login: (email, password) =>
    api.post("/auth/login", { email, password }).then((r) => r.data),
  me: () => api.get("/auth/me").then((r) => r.data),
  logout: (refresh_token) => api.post("/auth/logout", { refresh_token }),
  changePassword: (current_password, new_password) =>
    api.post("/auth/change-password", { current_password, new_password }),
};

export const sessionApi = {
  open: (payload) => api.post("/sessions", payload).then((r) => r.data),
  close: (id) => api.post(`/sessions/${id}/close`).then((r) => r.data),
  cancel: (id, reason) =>
    api.post(`/sessions/${id}/cancel`, null, { params: { reason } }),
  roster: (id) => api.get(`/sessions/${id}/roster`).then((r) => r.data),

  /**
   * Send one camera frame for recognition AND marking.
   *
   * multipart/form-data, because we are uploading binary image data. The
   * Content-Type header is deliberately overridden to undefined so the browser
   * sets it itself -- it must include the multipart boundary string, which we
   * cannot generate. Setting it manually is a classic cause of silent 422s.
   */
  markFace: (id, blob) => {
    const form = new FormData();
    form.append("frame", blob, "frame.jpg");
    return api
      .post(`/sessions/${id}/mark-face`, form, {
        headers: { "Content-Type": undefined },
      })
      .then((r) => r.data);
  },

  markManual: (id, student_id, status, notes) =>
    api
      .post(`/sessions/${id}/mark-manual`, { student_id, status, notes })
      .then((r) => r.data),
};

export const attendanceApi = {
  updateRecord: (recordId, status, notes) =>
    api.put(`/attendance/${recordId}`, { status, notes }).then((r) => r.data),
};

export const analyticsApi = {
  studentAttendance: (studentId) =>
    api.get(`/students/${studentId}/attendance`).then((r) => r.data),
  facultyAttendance: (facultyId) =>
    api.get(`/faculty/${facultyId}/attendance`).then((r) => r.data),
  defaulters: (offeringId, threshold) =>
    api
      .get(`/offerings/${offeringId}/defaulters`, { params: { threshold } })
      .then((r) => r.data),

  /* Chart analytics. `offeringDashboard` bundles all five charts into one
     request so they are guaranteed to describe the same moment. */
  offeringDashboard: (offeringId) =>
    api.get(`/analytics/offerings/${offeringId}/dashboard`).then((r) => r.data),
  daily: (offeringId, days = 30) =>
    api
      .get(`/analytics/offerings/${offeringId}/daily`, { params: { days } })
      .then((r) => r.data),
  monthly: (offeringId, months = 6) =>
    api
      .get(`/analytics/offerings/${offeringId}/monthly`, { params: { months } })
      .then((r) => r.data),
  studentPercentages: (offeringId) =>
    api.get(`/analytics/offerings/${offeringId}/students`).then((r) => r.data),
  subjectPercentages: (facultyId) =>
    api.get(`/analytics/faculty/${facultyId}/subjects`).then((r) => r.data),
  topAbsentees: (offeringId, limit = 10) =>
    api
      .get(`/analytics/offerings/${offeringId}/absentees`, { params: { limit } })
      .then((r) => r.data),
};

export const recognitionApi = {
  health: () => api.get("/recognition/health").then((r) => r.data),
  refreshIndex: () => api.post("/recognition/refresh-index").then((r) => r.data),
};
