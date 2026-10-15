$tests = @("conftest.py", "test_api.py", "test_units.py", "test_flows.py", "test_edge_cases.py", "test_refactor.py")
$checks = @("test_analytics.py", "test_attendance.py", "test_auth.py", "test_enrollment_e2e.py", "test_ml_01_preprocessing.py", "test_ml_02_detection.py", "test_ml_03_embedding.py", "test_ml_04_matching.py")

foreach ($f in $tests) { 
    if (Test-Path $f) { 
        Copy-Item -Path $f -Destination "tests\$f"
        Remove-Item -Path $f 
    } 
}
foreach ($f in $checks) { 
    if (Test-Path $f) { 
        Copy-Item -Path $f -Destination "checks\$f" 
        Remove-Item -Path $f
    } 
}
Write-Output "Files moved successfully"
