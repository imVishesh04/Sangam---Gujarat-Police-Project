import jsPDF from 'jspdf';
import autoTable from 'jspdf-autotable';
import { Alert, Detection, TrackPoint, Camera } from '../types';

export const pdfExportService = {
  // 1. Export Vehicle Trajectory Intelligence Dossier
  exportTrajectoryDossier(plate: string, trackPoints: TrackPoint[]) {
    const doc = new jsPDF({
      orientation: 'portrait',
      unit: 'mm',
      format: 'a4',
    });

    const now = new Date();
    const formattedDate = now.toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' });

    // Official Police Header
    doc.setFillColor(30, 58, 95); // Navy #1E3A5F
    doc.rect(0, 0, 210, 28, 'F');

    doc.setTextColor(255, 255, 255);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(14);
    doc.text('SANGAM — Gujarat Police CCTV & ANPR', 14, 12);

    doc.setFont('helvetica', 'normal');
    doc.setFontSize(9);
    doc.text('Vehicle route history', 14, 18);
    doc.text(`Prototype report | Generated: ${formattedDate} IST`, 14, 23);

    // Subject Box
    doc.setDrawColor(226, 232, 240);
    doc.setFillColor(248, 250, 252);
    doc.roundedRect(14, 34, 182, 26, 2, 2, 'FD');

    doc.setTextColor(15, 23, 42);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(10);
    doc.text('VEHICLE', 18, 41);

    doc.setFont('helvetica', 'normal');
    doc.setFontSize(9);
    doc.text(`Registration Plate: `, 18, 48);
    doc.setFont('helvetica', 'bold');
    doc.setTextColor(30, 58, 95);
    doc.text(plate, 50, 48);

    doc.setFont('helvetica', 'normal');
    doc.setTextColor(71, 85, 105);
    doc.text(`Total Checkpoints: ${trackPoints.length} sightings`, 18, 54);
    
    if (trackPoints.length > 0) {
      doc.text(`Origin: ${trackPoints[0].location_name}`, 110, 48);
      doc.text(`Last Sighting: ${trackPoints[trackPoints.length - 1].location_name}`, 110, 54);
    }

    // Trajectory Chronology Table
    const tableData = trackPoints.map((tp, index) => [
      `#${index + 1}`,
      new Date(tp.timestamp).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
      tp.camera_id,
      tp.location_name,
      tp.city || 'Gujarat',
      tp.confidence == null ? 'manual entry' : `${tp.confidence}%`
    ]);

    autoTable(doc, {
      startY: 66,
      head: [['Seq', 'Timestamp', 'Camera ID', 'Checkpoint Location', 'District / City', 'OCR Confidence']],
      body: tableData,
      theme: 'grid',
      headStyles: {
        fillColor: [30, 58, 95],
        textColor: [255, 255, 255],
        fontSize: 8.5,
        fontStyle: 'bold',
        halign: 'left',
      },
      bodyStyles: {
        textColor: [15, 23, 42],
        fontSize: 8,
        cellPadding: 3,
      },
      alternateRowStyles: {
        fillColor: [248, 250, 252],
      },
      columnStyles: {
        0: { halign: 'center', cellWidth: 14 },
        1: { halign: 'center', cellWidth: 24 },
        2: { fontStyle: 'bold', cellWidth: 26 },
        3: { cellWidth: 54 },
        4: { cellWidth: 22 },
        5: { halign: 'center', cellWidth: 20 },
        6: { halign: 'center', cellWidth: 22 },
      },
      margin: { left: 14, right: 14 },
    });

    // Signoff / Footer
    const finalY = (doc as any).lastAutoTable.finalY + 12;
    if (finalY < 260) {
      doc.setFontSize(8);
      doc.setTextColor(100, 116, 139);
      doc.text('Generated automatically by SANGAM from ANPR plate reads. Verify before official use.', 14, finalY);
      doc.text('Report ID: ' + Date.now().toString(36).toUpperCase(), 14, finalY + 5);
    }

    doc.save(`GUJARAT_POLICE_TRAIL_${plate.replace(/[^A-Z0-9]/g, '_')}_${Date.now()}.pdf`);
  },

  // 2. Export Live Alerts Log PDF
  exportAlertsLog(alerts: Alert[]) {
    const doc = new jsPDF({
      orientation: 'landscape',
      unit: 'mm',
      format: 'a4',
    });

    const now = new Date();
    const formattedDate = now.toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' });

    // Official Police Header
    doc.setFillColor(30, 58, 95);
    doc.rect(0, 0, 297, 24, 'F');

    doc.setTextColor(255, 255, 255);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(13);
    doc.text('SANGAM — Watchlist alerts', 14, 11);

    doc.setFont('helvetica', 'normal');
    doc.setFontSize(8.5);
    doc.text(`Watchlist matches and actions taken | Exported: ${formattedDate} IST`, 14, 18);

    const tableData = alerts.map((a, idx) => [
      `#${idx + 1}`,
      a.alert_id,
      new Date(a.timestamp).toLocaleString('en-IN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: 'short' }),
      a.matched_value,
      a.severity.toUpperCase(),
      a.reason,
      a.location_name,
      a.camera_id,
      a.is_acknowledged ? `ACKNOWLEDGED: ${a.acknowledged_by || 'Officer'}` : 'OPEN'
    ]);

    autoTable(doc, {
      startY: 30,
      head: [['#', 'Alert ID', 'Time (IST)', 'Plate / Target', 'Severity', 'Watchlist Reason', 'Camera Location', 'Node ID', 'Status']],
      body: tableData,
      theme: 'grid',
      headStyles: {
        fillColor: [30, 58, 95],
        textColor: [255, 255, 255],
        fontSize: 8,
        fontStyle: 'bold',
      },
      bodyStyles: {
        textColor: [15, 23, 42],
        fontSize: 7.5,
        cellPadding: 2.5,
      },
      alternateRowStyles: {
        fillColor: [248, 250, 252],
      },
      columnStyles: {
        0: { halign: 'center', cellWidth: 10 },
        1: { cellWidth: 26 },
        2: { cellWidth: 26 },
        3: { fontStyle: 'bold', cellWidth: 28 },
        4: { halign: 'center', fontStyle: 'bold', cellWidth: 20 },
        5: { cellWidth: 54 },
        6: { cellWidth: 50 },
        7: { cellWidth: 24 },
        8: { cellWidth: 32 },
      },
      margin: { left: 14, right: 14 },
    });

    doc.save(`GUJARAT_POLICE_ALERTS_${Date.now()}.pdf`);
  },

  // 3. Export Raw Detections Ingestion Log PDF
  exportDetectionsLog(detections: Detection[]) {
    const doc = new jsPDF({
      orientation: 'landscape',
      unit: 'mm',
      format: 'a4',
    });

    const now = new Date();
    const formattedDate = now.toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' });

    // Official Police Header
    doc.setFillColor(30, 58, 95);
    doc.rect(0, 0, 297, 24, 'F');

    doc.setTextColor(255, 255, 255);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(13);
    doc.text('SANGAM — Plate reads', 14, 11);

    doc.setFont('helvetica', 'normal');
    doc.setFontSize(8.5);
    doc.text(`ANPR plate reads | Exported: ${formattedDate} IST`, 14, 18);

    const tableData = detections.slice(0, 500).map((d, idx) => [
      `#${idx + 1}`,
      new Date(d.timestamp).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
      d.plate_number,
      d.confidence == null ? "manual" : `${d.confidence}%`,
      d.camera_id,
      d.location_name || 'N/A'
    ]);

    autoTable(doc, {
      startY: 30,
      head: [['#', 'Time (IST)', 'Plate Number', 'OCR Score', 'Camera ID', 'Checkpoint Location']],
      body: tableData,
      theme: 'grid',
      headStyles: {
        fillColor: [30, 58, 95],
        textColor: [255, 255, 255],
        fontSize: 8,
        fontStyle: 'bold',
      },
      bodyStyles: {
        textColor: [15, 23, 42],
        fontSize: 7.5,
        cellPadding: 2.5,
      },
      alternateRowStyles: {
        fillColor: [248, 250, 252],
      },
      columnStyles: {
        0: { halign: 'center', cellWidth: 12 },
        1: { halign: 'center', cellWidth: 22 },
        2: { fontStyle: 'bold', cellWidth: 32 },
        3: { halign: 'center', fontStyle: 'bold', cellWidth: 20 },
        4: { cellWidth: 24 },
        5: { cellWidth: 24 },
        6: { halign: 'center', cellWidth: 22 },
        7: { cellWidth: 28 },
        8: { cellWidth: 85 },
      },
      margin: { left: 14, right: 14 },
    });

    doc.save(`GUJARAT_POLICE_DETECTIONS_${Date.now()}.pdf`);
  },

  // 4. Export Camera Directory Grid PDF
  exportCamerasDirectory(cameras: Camera[]) {
    const doc = new jsPDF({
      orientation: 'landscape',
      unit: 'mm',
      format: 'a4',
    });

    const now = new Date();
    const formattedDate = now.toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' });

    doc.setFillColor(30, 58, 95);
    doc.rect(0, 0, 297, 24, 'F');

    doc.setTextColor(255, 255, 255);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(13);
    doc.text('SANGAM — Camera directory', 14, 11);

    doc.setFont('helvetica', 'normal');
    doc.setFontSize(8.5);
    doc.text(`Registered cameras | Exported: ${formattedDate} IST`, 14, 18);

    const tableData = cameras.map((c, idx) => [
      `#${idx + 1}`,
      c.camera_id,
      c.location_name,
      c.city,
      c.department,
      c.vendor,
      c.format,
      `${c.latitude.toFixed(4)}, ${c.longitude.toFixed(4)}`,
      c.status.toUpperCase()
    ]);

    autoTable(doc, {
      startY: 30,
      head: [['#', 'Node ID', 'Location Name', 'City', 'Department', 'Vendor', 'Format', 'Coordinates', 'Status']],
      body: tableData,
      theme: 'grid',
      headStyles: {
        fillColor: [30, 58, 95],
        textColor: [255, 255, 255],
        fontSize: 8,
        fontStyle: 'bold',
      },
      bodyStyles: {
        textColor: [15, 23, 42],
        fontSize: 7.5,
        cellPadding: 2.5,
      },
      alternateRowStyles: {
        fillColor: [248, 250, 252],
      },
      columnStyles: {
        0: { halign: 'center', cellWidth: 12 },
        1: { fontStyle: 'bold', cellWidth: 28 },
        2: { cellWidth: 60 },
        3: { cellWidth: 26 },
        4: { cellWidth: 50 },
        5: { cellWidth: 30 },
        6: { cellWidth: 20 },
        7: { cellWidth: 30 },
        8: { halign: 'center', fontStyle: 'bold', cellWidth: 14 },
      },
      margin: { left: 14, right: 14 },
    });

    doc.save(`GUJARAT_POLICE_CAMERAS_${Date.now()}.pdf`);
  }
};
