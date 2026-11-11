param(
  [Parameter(Mandatory=$true)][string]$InputWorkbook,
  [Parameter(Mandatory=$true)][string]$OutputPdf,
  [switch]$FitToA4
)
$ErrorActionPreference = 'Stop'
$inputPath = (Resolve-Path -LiteralPath $InputWorkbook).Path
$outputPath = [System.IO.Path]::GetFullPath($OutputPdf)
$excel = $null
$workbook = $null
try {
  # A separate hidden Excel instance, read-only workbook, no link updates.
  $excel = New-Object -ComObject Excel.Application
  $excel.Visible = $false
  $excel.DisplayAlerts = $false
  $excel.AutomationSecurity = 3
  $workbook = $excel.Workbooks.Open($inputPath, 0, $true)
  $sheet = $workbook.Worksheets.Item(1)
  if ($FitToA4) {
    $sheet.PageSetup.PrintArea = '$A$1:$H$15'
    $sheet.PageSetup.PaperSize = 9
    $sheet.PageSetup.Orientation = 1
    $sheet.PageSetup.Zoom = $false
    $sheet.PageSetup.FitToPagesWide = 1
    $sheet.PageSetup.FitToPagesTall = 1
  }
  Write-Output ('Excel opened: sheets={0}; drawings={1}; thickness={2}; width={3}' -f $workbook.Worksheets.Count,$sheet.Shapes.Count,$sheet.Range('G9').Value2,$sheet.Range('G10').Value2)
  $workbook.ExportAsFixedFormat(0, $outputPath)
  Write-Output ('PDF: ' + $outputPath)
} finally {
  if ($workbook) { $workbook.Close($false) }
  if ($excel) { $excel.Quit() }
  if ($workbook) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($workbook) }
  if ($excel) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($excel) }
  [GC]::Collect()
  [GC]::WaitForPendingFinalizers()
}
