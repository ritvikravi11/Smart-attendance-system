import { Check, ImagePlus, Plus, ShieldCheck, Users, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { errorMessage } from "../api/client";
import { studentsApi } from "../api/endpoints";
import { Page, PageHeader } from "../components/layout/AppShell";
import {
  Alert,
  Button,
  Card,
  EmptyState,
  Input,
  Skeleton,
} from "../components/ui/primitives";

const EMPTY_FORM = {
  full_name: "",
  email: "",
  password: "",
  roll_number: "",
  department_id: "",
  semester: "1",
  phone: "",
  admission_year: "",
};

function LoadingRows() {
  return (
    <div className="divide-y divide-ink-900/5 dark:divide-white/5">
      {[0, 1, 2, 3].map((row) => (
        <div key={row} className="grid gap-4 px-5 py-4 sm:grid-cols-4">
          <Skeleton className="h-4 w-36" />
          <Skeleton className="h-4 w-28" />
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-4 w-20" />
        </div>
      ))}
    </div>
  );
}

function BiometricModal({ student, status, onClose, onChanged }) {
  const [consentBusy, setConsentBusy] = useState(false);
  const [files, setFiles] = useState([]);
  const [submitting, setSubmitting] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState(null);
  const [message, setMessage] = useState(null);

  const hasConsent = status?.has_valid_consent === true;
  const enrolled = status?.face_enrolled === true;

  async function updateConsent(granted) {
    setConsentBusy(true);
    setError(null);
    setMessage(null);
    try {
      await studentsApi.consent(
        student.student_id,
        granted,
        "Consent recorded through the administrator portal"
      );
      await onChanged();
      setMessage(granted ? "Biometric consent granted." : "Consent revoked and face data erased.");
      if (!granted) setFiles([]);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setConsentBusy(false);
    }
  }

  function chooseFiles(event) {
    setError(null);
    const selected = Array.from(event.target.files ?? []);
    const allowed = selected.filter((file) =>
      ["image/jpeg", "image/png"].includes(file.type)
    );
    if (allowed.length !== selected.length) {
      setError("Only JPEG and PNG images are supported.");
    }
    if (allowed.reduce((total, file) => total + file.size, 0) > 40 * 1024 * 1024) {
      setError("The selected images exceed the 40 MB total upload limit.");
      setFiles([]);
      event.target.value = "";
      return;
    }
    setFiles(allowed);
    event.target.value = "";
  }

  async function enroll() {
    if (!hasConsent) {
      setError("Grant explicit biometric consent before enrolling a face.");
      return;
    }
    if (files.length < 5 || files.length > 15) {
      setError("Select between 5 and 15 images.");
      return;
    }
    setSubmitting(true);
    setProgress(0);
    setError(null);
    setMessage(null);
    try {
      const result = await studentsApi.enrollFaces(
        student.student_id,
        files,
        (event) => event.total && setProgress(Math.round((event.loaded / event.total) * 100))
      );
      await onChanged();
      setFiles([]);
      setMessage(
        `Face enrollment confirmed: ${result.images_accepted} image${result.images_accepted === 1 ? "" : "s"} accepted.`
      );
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-ink-950/70 px-4 py-8 backdrop-blur-sm">
      <Card className="mx-auto max-w-2xl">
        <div className="flex items-center justify-between border-b border-ink-900/5 px-5 py-4 dark:border-white/5">
          <div>
            <p className="eyebrow mb-1">Biometric attendance</p>
            <h2 className="font-display text-title text-ink-900 dark:text-ink-100">
              {student.full_name}
            </h2>
            <p className="text-[13px] text-ink-500 dark:text-ink-400">{student.roll_number}</p>
          </div>
          <button type="button" onClick={onClose} className="rounded-lg p-2 text-ink-400 hover:bg-ink-900/5 dark:hover:bg-white/5" aria-label="Close biometric management">
            <X className="h-4 w-4" aria-hidden />
          </button>
        </div>
        <div className="space-y-5 p-5">
          {error && <Alert variant="error">{error}</Alert>}
          {message && <Alert variant="success">{message}</Alert>}
          <div className="rounded-lg border border-ink-900/10 p-4 text-sm dark:border-white/10">
            <div className="flex items-start gap-3">
              <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-accent" aria-hidden />
              <div>
                <p className="font-medium text-ink-900 dark:text-ink-100">Explicit biometric consent</p>
                <p className="mt-1 text-ink-500 dark:text-ink-400">
                  Face images will be processed to create a biometric template for attendance recognition.
                  Raw enrollment images are not retained. Revoking consent removes the stored face enrollment.
                </p>
                <p className="mt-2 text-[13px] font-medium text-ink-700 dark:text-ink-300">
                  Current state: {hasConsent ? "Consent granted" : "Consent required"}
                  {enrolled && " · Face enrolled"}
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {!hasConsent ? (
                    <Button size="sm" loading={consentBusy} icon={Check} onClick={() => updateConsent(true)}>
                      Grant consent
                    </Button>
                  ) : (
                    <Button size="sm" variant="danger" loading={consentBusy} onClick={() => updateConsent(false)}>
                      Revoke consent
                    </Button>
                  )}
                </div>
              </div>
            </div>
          </div>
          <div>
            <p className="font-medium text-ink-900 dark:text-ink-100">Enroll face images</p>
            <p className="mt-1 text-[13px] text-ink-500 dark:text-ink-400">
              Choose 5–15 clear JPEG or PNG images containing only this student’s face.
            </p>
            <label className="mt-3 flex cursor-pointer items-center justify-center gap-2 rounded-lg border border-dashed border-ink-900/20 px-4 py-6 text-sm text-ink-500 hover:border-accent dark:border-white/15 dark:text-ink-400">
              <ImagePlus className="h-5 w-5" aria-hidden />
              Choose enrollment images
              <input type="file" accept="image/jpeg,image/png" multiple className="sr-only" onChange={chooseFiles} disabled={!hasConsent || submitting} />
            </label>
            <p className="mt-2 text-[13px] text-ink-500 dark:text-ink-400">
              {files.length} selected
              {files.length < 5 && ` · Select ${5 - files.length} more`}
              {files.length > 15 && " · Remove images until 15 or fewer remain"}
            </p>
            {files.length > 0 && (
              <ul className="mt-2 max-h-32 space-y-1 overflow-y-auto text-[13px] text-ink-600 dark:text-ink-300">
                {files.map((file, index) => (
                  <li key={`${file.name}-${index}`} className="flex items-center justify-between gap-2">
                    <span className="truncate">{file.name}</span>
                    <button type="button" className="text-ink-400 hover:text-absent" onClick={() => setFiles((current) => current.filter((_, i) => i !== index))} aria-label={`Remove ${file.name}`}>
                      <X className="h-3.5 w-3.5" aria-hidden />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {submitting && <p className="mt-2 text-[13px] text-accent">Uploading: {progress}%</p>}
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={onClose}>Close</Button>
            <Button type="button" loading={submitting} disabled={!hasConsent || files.length < 5 || files.length > 15} onClick={enroll}>
              {enrolled ? "Update enrollment" : "Enroll face"}
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
}

function StudentForm({ departments, onClose, onCreated }) {
  const [form, setForm] = useState(EMPTY_FORM);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  function update(field) {
    return (event) =>
      setForm((current) => ({ ...current, [field]: event.target.value }));
  }

  async function submit(event) {
    event.preventDefault();
    setError(null);
    if (!form.full_name.trim() || !form.roll_number.trim()) {
      setError("Name and roll number are required.");
      return;
    }
    if (form.password.length < 8) {
      setError("Password must contain at least 8 characters.");
      return;
    }
    if (!form.department_id) {
      setError("Select a department.");
      return;
    }

    setSubmitting(true);
    try {
      const student = await studentsApi.create({
        ...form,
        full_name: form.full_name.trim(),
        roll_number: form.roll_number.trim(),
        department_id: Number(form.department_id),
        semester: Number(form.semester),
        admission_year: form.admission_year
          ? Number(form.admission_year)
          : null,
        phone: form.phone.trim() || null,
      });
      await onCreated();
      onClose(`Student created successfully. ID: ${student.student_id}`);
      setForm(EMPTY_FORM);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-ink-950/70 px-4 py-8 backdrop-blur-sm">
      <Card className="mx-auto max-w-2xl">
        <div className="flex items-center justify-between border-b border-ink-900/5 px-5 py-4 dark:border-white/5">
          <div>
            <p className="eyebrow mb-1">Administration</p>
            <h2 className="font-display text-title text-ink-900 dark:text-ink-100">
              Add Student
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-2 text-ink-400 hover:bg-ink-900/5 dark:hover:bg-white/5"
            aria-label="Close add student form"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        </div>

        <form onSubmit={submit} className="space-y-4 p-5">
          {error && <Alert variant="error">{error}</Alert>}
          <div className="grid gap-4 sm:grid-cols-2">
            <Input label="Full name" value={form.full_name} onChange={update("full_name")} required />
            <Input label="Email" type="email" value={form.email} onChange={update("email")} required />
            <Input
              label="Temporary password"
              type="password"
              minLength={8}
              value={form.password}
              onChange={update("password")}
              required
            />
            <Input label="Roll number" value={form.roll_number} onChange={update("roll_number")} required />
            <label className="space-y-1.5 text-[13px] font-medium text-ink-700 dark:text-ink-300">
              Department
              <select
                className="block w-full rounded-lg border-0 bg-white px-3.5 py-2.5 text-sm text-ink-900 ring-1 ring-inset ring-ink-900/10 focus:ring-2 focus:ring-accent dark:bg-ink-800 dark:text-ink-100 dark:ring-white/10"
                value={form.department_id}
                onChange={update("department_id")}
                required
              >
                <option value="">Select department</option>
                {departments.map((department) => (
                  <option key={department.id} value={department.id}>
                    {department.code} — {department.name}
                  </option>
                ))}
              </select>
            </label>
            <Input label="Semester" type="number" min="1" max="8" value={form.semester} onChange={update("semester")} required />
            <Input label="Phone (optional)" value={form.phone} onChange={update("phone")} />
            <Input label="Admission year (optional)" type="number" min="1900" max="2200" value={form.admission_year} onChange={update("admission_year")} />
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" loading={submitting} icon={Plus}>
              Create student
            </Button>
          </div>
        </form>
      </Card>
    </div>
  );
}

export default function StudentList() {
  const [students, setStudents] = useState([]);
  const [departments, setDepartments] = useState([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [formOpen, setFormOpen] = useState(false);
  const [success, setSuccess] = useState(null);
  const [biometricStudent, setBiometricStudent] = useState(null);
  const [biometricStatus, setBiometricStatus] = useState({});
  const [statusLoading, setStatusLoading] = useState({});

  const loadStudents = useCallback(async () => {
    const data = await studentsApi.list();
    setStudents(data.students ?? []);
    setTotal(data.total ?? data.students?.length ?? 0);
  }, []);

  const loadFaceStatus = useCallback(async (studentId) => {
    setStatusLoading((current) => ({ ...current, [studentId]: true }));
    try {
      const status = await studentsApi.faceStatus(studentId);
      setBiometricStatus((current) => ({ ...current, [studentId]: status }));
      return status;
    } catch (err) {
      setError(errorMessage(err));
      return null;
    } finally {
      setStatusLoading((current) => ({ ...current, [studentId]: false }));
    }
  }, []);

  useEffect(() => {
    document.title = "Students — Register";
    Promise.all([loadStudents(), studentsApi.departments()])
      .then(([, departmentData]) => setDepartments(departmentData))
      .catch((err) => setError(errorMessage(err)))
      .finally(() => setLoading(false));
  }, [loadStudents]);

  useEffect(() => {
    if (students.length) {
      Promise.all(students.map((student) => loadFaceStatus(student.student_id)));
    }
  }, [students, loadFaceStatus]);

  const filteredStudents = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return students;
    return students.filter((student) =>
      [student.full_name, student.email, student.roll_number, student.student_id]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(normalized))
    );
  }, [query, students]);

  async function handleCreated() {
    await loadStudents();
  }

  function closeForm(message = null) {
    setFormOpen(false);
    if (message) setSuccess(message);
  }

  async function openBiometric(student) {
    setError(null);
    setBiometricStudent(student);
    await loadFaceStatus(student.student_id);
  }

  return (
    <Page>
      <PageHeader
        eyebrow="Administration"
        title="Student List"
        description={`${total} ${total === 1 ? "student" : "students"} in the system`}
        action={
          <Button icon={Plus} onClick={() => setFormOpen(true)}>
            Add Student
          </Button>
        }
      />

      <div className="space-y-5">
        <Input
          aria-label="Search students"
          placeholder="Search by name, email, or roll number"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="max-w-md"
        />

        {success && (
          <Alert variant="success" title="Student added">
            {success}
          </Alert>
        )}

        {error && (
          <Alert variant="error" title="Could not load students">
            {error}
          </Alert>
        )}

        <Card className="overflow-hidden">
          <div className="flex items-center justify-between border-b border-ink-900/5 px-5 py-4 dark:border-white/5">
            <div className="flex items-center gap-2">
              <Users className="h-4 w-4 text-ink-400" aria-hidden />
              <h2 className="font-display text-title text-ink-900 dark:text-ink-100">
                Students
              </h2>
            </div>
            <span className="font-mono text-[11px] text-ink-400">
              {filteredStudents.length} shown
            </span>
          </div>

          {loading ? (
            <LoadingRows />
          ) : filteredStudents.length === 0 ? (
            <EmptyState
              icon={Users}
              title={query ? "No matching students" : "No students yet"}
              description={query ? "Try a different search." : "Add a student to get started."}
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[52rem] text-left">
                <thead className="border-b border-ink-900/5 dark:border-white/5">
                  <tr className="eyebrow">
                    <th className="px-5 py-3 font-medium">ID</th>
                    <th className="px-5 py-3 font-medium">Student</th>
                    <th className="px-5 py-3 font-medium">Roll number</th>
                    <th className="px-5 py-3 font-medium">Class</th>
                    <th className="px-5 py-3 font-medium">Face data</th>
                    <th className="sticky right-0 z-10 bg-white px-5 py-3 font-medium shadow-[-8px_0_12px_-12px_rgba(0,0,0,0.35)] dark:bg-ink-900">
                      Actions
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-ink-900/5 dark:divide-white/5">
                  {filteredStudents.map((student) => (
                    <tr key={student.student_id} className="text-sm">
                      <td className="px-5 py-4 font-mono text-[13px] text-ink-500 dark:text-ink-400">{student.student_id}</td>
                      <td className="sticky right-0 z-10 bg-white px-5 py-4 shadow-[-8px_0_12px_-12px_rgba(0,0,0,0.35)] dark:bg-ink-900">
                        <p className="font-medium text-ink-900 dark:text-ink-100">{student.full_name}</p>
                        <p className="text-[13px] text-ink-500 dark:text-ink-400">{student.email}</p>
                      </td>
                      <td className="px-5 py-4 font-mono text-[13px] text-ink-500 dark:text-ink-400">{student.roll_number}</td>
                      <td className="px-5 py-4 text-[13px] text-ink-500 dark:text-ink-400">{student.department_name} · Sem {student.semester}</td>
                      <td className="px-5 py-4 text-[13px] text-ink-500 dark:text-ink-400">
                        {statusLoading[student.student_id]
                          ? "Checking…"
                          : biometricStatus[student.student_id]?.has_valid_consent === false
                            ? "Consent required"
                            : biometricStatus[student.student_id]?.face_enrolled || student.face_enrolled
                              ? "Enrolled"
                              : "Not enrolled"}
                      </td>
                      <td className="px-5 py-4">
                        <Button size="sm" variant="secondary" onClick={() => openBiometric(student)}>
                          Manage biometrics
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      {formOpen && (
        <StudentForm
          departments={departments}
          onClose={closeForm}
          onCreated={handleCreated}
        />
      )}
      {biometricStudent && (
        <BiometricModal
          student={biometricStudent}
          status={biometricStatus[biometricStudent.student_id]}
          onClose={() => setBiometricStudent(null)}
          onChanged={() => loadFaceStatus(biometricStudent.student_id)}
        />
      )}
    </Page>
  );
}
