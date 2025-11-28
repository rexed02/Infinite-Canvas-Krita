from krita import Krita, DockWidget, DockWidgetFactoryBase
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QSizePolicy, QApplication, 
                             QToolButton, QHBoxLayout, QLabel, QSplitter, 
                             QStackedLayout, QComboBox, QCheckBox, QOpenGLWidget,
                             QAbstractScrollArea, QMdiArea, QSlider, QColorDialog, QPushButton)
from PyQt5.QtCore import Qt, QTimer, QObject, QEvent, QPointF, QPoint, QRect, QRectF, pyqtSignal, QSize
from PyQt5.QtGui import QPainter, QPen, QPixmap, QColor, QImage, QBrush, QPainterPath, QTransform
import time
import json
import os
from math import cos, sin, ceil

# --- CONFIGURACIÓN ---
BASE_CAMERA_SIZE = 200
GRID_SIZE = 12
MAX_BUFFER_SIZE = 2500 

# Mapeo de modos de fusión
BLEND_MODES_MAP = {
    'normal': QPainter.CompositionMode_SourceOver,
    'multiply': QPainter.CompositionMode_Multiply,
    'screen': QPainter.CompositionMode_Screen,
    'overlay': QPainter.CompositionMode_Overlay,
    'darken': QPainter.CompositionMode_Darken,
    'lighten': QPainter.CompositionMode_Lighten,
    'color_dodge': QPainter.CompositionMode_ColorDodge,
    'color_burn': QPainter.CompositionMode_ColorBurn,
    'hard_light': QPainter.CompositionMode_HardLight,
    'soft_light': QPainter.CompositionMode_SoftLight,
    'difference': QPainter.CompositionMode_Difference,
    'exclusion': QPainter.CompositionMode_Exclusion,
    'plus': QPainter.CompositionMode_Plus,
    'xor': QPainter.CompositionMode_Xor,
}

# =========================================================================================
# CLASE 1: OVERLAY
# =========================================================================================
class OverlayWidget(QWidget):
    def __init__(self, docker_ref, parent=None):
        super().__init__(parent)
        self.docker = docker_ref
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.live_stroke_buffer = None
        self.buffer_transform = QTransform()
        self.has_content = False
        self.render_buffer = None
        self.global_opacity = 1.0
        self.crop_enabled = False
        self.outline_enabled = False
        self.no_color_enabled = False
        self.source_mode = 0 
        self.overlay_color = QColor(0, 0, 255, 255) 

    def set_overlay_settings(self, opacity, crop, outline, color, no_color, mode):
        self.global_opacity = opacity
        self.crop_enabled = crop
        self.outline_enabled = outline
        self.overlay_color = color
        self.no_color_enabled = no_color
        self.source_mode = mode
        self.update()

    def ensure_buffers(self):
        size = self.size()
        if self.live_stroke_buffer is None or self.live_stroke_buffer.size() != size:
            self.live_stroke_buffer = QPixmap(size)
            self.live_stroke_buffer.fill(Qt.transparent)
            self.has_content = False   
        if self.render_buffer is None or self.render_buffer.size() != size:
            self.render_buffer = QPixmap(size)

    def clear_live_buffer(self):
        if self.live_stroke_buffer and self.has_content:
            self.live_stroke_buffer.fill(Qt.transparent)
            self.has_content = False
            self.update()

    def handle_live_patch(self, image, doc_x, doc_y, doc_w, doc_h, current_transform):
        self.ensure_buffers()
        if current_transform != self.buffer_transform:
            self.live_stroke_buffer.fill(Qt.transparent)
            self.buffer_transform = current_transform
        painter = QPainter(self.live_stroke_buffer)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.setTransform(current_transform)
        rect_doc = QRectF(doc_x, doc_y, doc_w, doc_h).adjusted(-0.5, -0.5, 0.5, 0.5)
        painter.drawImage(rect_doc, image)
        painter.end()
        self.has_content = True
        self.update()

    def paintEvent(self, event):
        self.ensure_buffers()
        self.render_buffer.fill(Qt.transparent)
        doc = Krita.instance().activeDocument()
        view = Krita.instance().activeWindow().activeView()
        if not doc or not view: return
        canvas = view.canvas()
        res = doc.resolution() or 72.0
        zoom = canvas.zoomLevel()
        rotation = canvas.rotation()
        mirror = canvas.mirror()
        scale_factor = (72.0 / res) * zoom
        t_flake = view.flakeToCanvasTransform()
        origin = t_flake.map(QPointF(0.0, 0.0))
        current_transform = QTransform()
        current_transform.translate(origin.x(), origin.y())
        current_transform.rotate(rotation)
        sx = -scale_factor if mirror else scale_factor
        sy = scale_factor
        current_transform.scale(sx, sy)

        if self.has_content:
            if current_transform != self.buffer_transform:
                self.clear_live_buffer()

        painter = QPainter(self.render_buffer)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        rect_doc_static = QRectF(0.0, 0.0, float(doc.width()), float(doc.height()))
        path_doc_static_local = QPainterPath()
        path_doc_static_local.addRect(rect_doc_static)
        path_doc_screen = current_transform.map(path_doc_static_local)

        rect_hole = rect_doc_static 
        if self.source_mode == 0: 
            if doc.activeNode():
                b = doc.activeNode().bounds()
                if b.width() > 0:
                    rect_hole = QRectF(b.x(), b.y(), b.width(), b.height())
        else:
            root = doc.rootNode()
            if root:
                b = root.bounds()
                if b.width() > 0:
                    rect_hole = QRectF(b.x(), b.y(), b.width(), b.height())

        path_hole_local = QPainterPath()
        path_hole_local.addRect(rect_hole)
        path_hole_screen = current_transform.map(path_hole_local)

        path_full_screen = QPainterPath()
        path_full_screen.addRect(QRectF(self.rect()))
        
        path_blue_area = path_full_screen.subtracted(path_hole_screen)
        path_outside_canvas = path_full_screen.subtracted(path_doc_screen)

        if self.crop_enabled:
            painter.setClipPath(path_outside_canvas)

        if not self.no_color_enabled:
            painter.setBrush(self.overlay_color) 
            painter.setPen(Qt.NoPen)
            painter.drawPath(path_blue_area)

        vp = self.docker.main_viewport
        vs = self.docker.view_state
        if vp and vs.valid and vp.base_pixmap:
            painter.save()
            painter.setTransform(current_transform)
            target_rect = QRectF(vs.src_rect)
            src_rect = QRectF(vp.base_pixmap.rect())
            painter.drawPixmap(target_rect, vp.base_pixmap, src_rect)
            painter.restore()

        if self.live_stroke_buffer and self.has_content:
            painter.drawPixmap(0, 0, self.live_stroke_buffer)

        if self.crop_enabled:
            painter.setClipping(False)

        if self.outline_enabled:
            pen = QPen(Qt.black)
            pen.setWidth(2)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path_doc_screen)

        painter.end()
        final_painter = QPainter(self)
        final_painter.setOpacity(self.global_opacity)
        final_painter.drawPixmap(0, 0, self.render_buffer)

    def resizeEvent(self, event):
        self.live_stroke_buffer = None
        self.render_buffer = None
        super().resizeEvent(event)

# =========================================================================================
# CLASE 2: VIEWPORT
# =========================================================================================
class MainViewportWidget(QOpenGLWidget):
    contentChanged = pyqtSignal()
    def __init__(self, parent=None):
        super().__init__(parent)
        self.base_pixmap = None     
        self.trail_buffer = None    
        self.cursor_rect = None
        self.show_reticle = True 
        self.grid_brush = self._create_grid_brush()

    def initializeGL(self): pass

    def _create_grid_brush(self):
        size = GRID_SIZE
        pixmap = QPixmap(size * 2, size * 2)
        pixmap.fill(QColor(220, 220, 220))
        painter = QPainter(pixmap)
        painter.fillRect(0, 0, size, size, QColor(255, 255, 255))
        painter.fillRect(size, size, size, size, QColor(255, 255, 255))
        painter.end()
        return QBrush(pixmap)

    def init_buffers(self, width, height):
        if width <= 0 or height <= 0: return
        self.trail_buffer = QPixmap(width, height)
        self.trail_buffer.fill(Qt.transparent)

    def set_base_background(self, pixmap):
        self.base_pixmap = pixmap
        if pixmap is None:
            self.update()
            self.contentChanged.emit()
            return
        if self.trail_buffer:
            if self.trail_buffer.size() != pixmap.size():
                self.trail_buffer = QPixmap(pixmap.size())
            self.trail_buffer.fill(Qt.transparent)
        self.update()
        self.contentChanged.emit()

    def update_cursor_pos(self, cursor_rect):
        self.cursor_rect = cursor_rect
        self.update()

    def stamp_trail(self, patch_image, dest_rect, cursor_rect):
        int_rect = dest_rect.toRect()
        if self.base_pixmap:
            painter_base = QPainter(self.base_pixmap)
            painter_base.setRenderHint(QPainter.Antialiasing, False)
            painter_base.setCompositionMode(QPainter.CompositionMode_Source)
            painter_base.drawImage(int_rect, patch_image)
            painter_base.end()
        if self.trail_buffer:
            painter_trail = QPainter(self.trail_buffer)
            painter_trail.setRenderHint(QPainter.Antialiasing, False)
            painter_trail.setCompositionMode(QPainter.CompositionMode_Source)
            painter_trail.drawImage(int_rect, patch_image)
            painter_trail.end()
        self.cursor_rect = cursor_rect
        self.update() 
        self.contentChanged.emit()

    def set_reticle_visible(self, visible):
        self.show_reticle = visible
        self.update()
        self.contentChanged.emit()

    def paintGL(self):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.fillRect(self.rect(), self.grid_brush)
        if self.base_pixmap and not self.base_pixmap.isNull():
            w_buf = self.base_pixmap.width()
            h_buf = self.base_pixmap.height()
            w_wid = self.width()
            h_wid = self.height()
            if w_buf > 0 and h_buf > 0:
                scale = min(w_wid / w_buf, h_wid / h_buf)
            else:
                scale = 1.0
            draw_w = int(w_buf * scale)
            draw_h = int(h_buf * scale)
            x = (w_wid - draw_w) // 2
            y = (h_wid - draw_h) // 2
            target_rect = QRect(x, y, draw_w, draw_h)
            source_rect = self.base_pixmap.rect() 
            painter.drawPixmap(target_rect, self.base_pixmap, source_rect)
            if self.trail_buffer:
                painter.drawPixmap(target_rect, self.trail_buffer, source_rect)
            if self.show_reticle and self.cursor_rect:
                painter.setRenderHint(QPainter.Antialiasing, False)
                pen = QPen(QColor(0, 120, 255))
                pen.setWidth(2)
                painter.setPen(pen)
                cx = (self.cursor_rect.x() * scale) + x
                cy = (self.cursor_rect.y() * scale) + y
                cw = self.cursor_rect.width() * scale
                ch = self.cursor_rect.height() * scale
                painter.drawRect(QRectF(cx, cy, cw, ch))

    def resizeGL(self, w, h):
        self.update()

# =========================================================================================
# CLASE 3: PREVIEW
# =========================================================================================
class CameraPreviewWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = None
        self.grid_brush = self._create_grid_brush()
        self.setFixedSize(300, 300) 
    def _create_grid_brush(self):
        size = GRID_SIZE
        pixmap = QPixmap(size * 2, size * 2)
        pixmap.fill(QColor(220, 220, 220))
        painter = QPainter(pixmap)
        painter.fillRect(0, 0, size, size, QColor(255, 255, 255))
        painter.fillRect(size, size, size, size, QColor(255, 255, 255))
        painter.end()
        return QBrush(pixmap)
    def update_image(self, image):
        self.image = image
        self.repaint() 
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.grid_brush)
        if self.image and not self.image.isNull():
            scaled_img = self.image.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = (self.width() - scaled_img.width()) // 2
            y = (self.height() - scaled_img.height()) // 2
            painter.drawImage(x, y, scaled_img)

# =========================================================================================
# CLASE 4: INTERCEPTOR
# =========================================================================================
class ViewState:
    def __init__(self):
        self.src_rect = QRect(0, 0, 100, 100) 
        self.scale = 1.0                      
        self.offset_x = 0.0                   
        self.offset_y = 0.0                   
        self.valid = False
        self.last_bounds_hash = None

class InputInterceptor(QObject):
    stroke_finished = pyqtSignal()
    live_patch_ready = pyqtSignal(QImage, float, float, float, float, QTransform) 

    def __init__(self, view_state, main_viewport, camera_preview):
        super().__init__()
        self.view_state = view_state
        self.main_viewport = main_viewport
        self.camera_preview = camera_preview
        self.active = False
        self.app_ref = Krita.instance()
        self.size_multiplier = 1
        self.last_process_time = 0.0
        self.min_interval = 0.010 
        self.is_drawing = False
        self.source_mode = 0 # 0: Layer, 1: Full

    def set_multiplier(self, mult):
        self.size_multiplier = mult
    def set_mode(self, mode):
        self.source_mode = mode

    def eventFilter(self, obj, event):
        if not self.active: return False
        etype = event.type()
        
        if not self.view_state.valid: return False
        
        modifiers = QApplication.keyboardModifiers()
        is_navigating = (modifiers & (Qt.ControlModifier | Qt.AltModifier)) or \
                        (QApplication.mouseButtons() == Qt.MiddleButton)

        if is_navigating:
            self.is_drawing = False
            return False

        if etype in [QEvent.MouseButtonPress, QEvent.TabletPress]:
            if event.button() == Qt.LeftButton:
                self.is_drawing = True
                self.process_draw(event)
            return False 

        if etype in [QEvent.MouseButtonRelease, QEvent.TabletRelease]:
            if self.is_drawing:
                self.is_drawing = False
                self.stroke_finished.emit()
            return False

        if etype in [QEvent.MouseMove, QEvent.TabletMove]:
            if self.is_drawing:
                self.process_draw(event)
            else:
                self.process_hover(event)
            return False
            
        return False

    def _calculate_geometry(self, global_pos):
        doc_pt = self.map_pos_to_document_absolute(global_pos)
        if not doc_pt: return None
        vs = self.view_state
        rel_x = doc_pt.x() - vs.src_rect.x()
        rel_y = doc_pt.y() - vs.src_rect.y()
        center_widget_x = (rel_x * vs.scale) 
        center_widget_y = (rel_y * vs.scale) 
        crop_size = int(BASE_CAMERA_SIZE * self.size_multiplier)
        crop_x = int(doc_pt.x() - crop_size / 2)
        crop_y = int(doc_pt.y() - crop_size / 2)
        patch_display_size = crop_size * vs.scale
        dest_x = center_widget_x - (patch_display_size / 2.0)
        dest_y = center_widget_y - (patch_display_size / 2.0)
        dest_rect = QRectF(dest_x, dest_y, patch_display_size, patch_display_size)
        return (crop_x, crop_y, crop_size, dest_rect)

    def process_hover(self, event):
        now = time.time()
        if (now - self.last_process_time) < 0.005: return
        self.last_process_time = now
        geom = self._calculate_geometry(event.globalPos())
        if not geom: return
        _, _, _, dest_rect = geom
        self.main_viewport.update_cursor_pos(dest_rect)

    def get_manual_projection(self, doc, x, y, w, h):
        view_rect = QRect(x, y, w, h)
        final_image = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        final_image.fill(Qt.transparent)
        painter = QPainter(final_image)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)

        def render_node_recursive(node):
            for child in node.childNodes():
                if not child.visible(): continue
                if "Group" in child.type():
                    render_node_recursive(child)
                else:
                    layer_bounds = child.bounds()
                    if layer_bounds.isEmpty(): continue
                    rect_visible = view_rect.intersected(layer_bounds)
                    if rect_visible.isEmpty(): continue
                    pixel_data = child.pixelData(
                        rect_visible.x(), rect_visible.y(), 
                        rect_visible.width(), rect_visible.height()
                    )
                    if not pixel_data: continue
                    rw = rect_visible.width()
                    rh = rect_visible.height()
                    expected_len = rw * rh
                    layer_img = None
                    if len(pixel_data) == expected_len * 4:
                        layer_img = QImage(pixel_data, rw, rh, QImage.Format_RGBA8888).rgbSwapped()
                    elif len(pixel_data) == expected_len * 8:
                        layer_img = QImage(pixel_data, rw, rh, rw * 8, QImage.Format_RGBA64).rgbSwapped()
                    if layer_img and not layer_img.isNull():
                        if layer_img.format() != QImage.Format_ARGB32_Premultiplied:
                            layer_img = layer_img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
                        target_x = rect_visible.x() - view_rect.x()
                        target_y = rect_visible.y() - view_rect.y()
                        painter.setOpacity(child.opacity() / 255.0)
                        mode_str = child.blendingMode()
                        comp_mode = BLEND_MODES_MAP.get(mode_str, QPainter.CompositionMode_SourceOver)
                        painter.setCompositionMode(comp_mode)
                        painter.drawImage(target_x, target_y, layer_img)

        root = doc.rootNode()
        if root:
            render_node_recursive(root)
        painter.end()
        return final_image

    def process_draw(self, event):
        now = time.time()
        if (now - self.last_process_time) < self.min_interval: return
        self.last_process_time = now
        geom = self._calculate_geometry(event.globalPos())
        if not geom: return
        crop_x, crop_y, crop_size, dest_rect = geom
        doc = self.app_ref.activeDocument()
        qimg_patch = None
        if doc:
            if self.source_mode == 0: 
                node = doc.activeNode()
                if node:
                    pixel_data = node.pixelData(crop_x, crop_y, crop_size, crop_size)
                    len_data = len(pixel_data)
                    if len_data == crop_size * crop_size * 4:
                        qimg_patch = QImage(pixel_data, crop_size, crop_size, QImage.Format_RGBA8888).rgbSwapped()
                    elif len_data == crop_size * crop_size * 8:
                        qimg_patch = QImage(pixel_data, crop_size, crop_size, crop_size * 8, QImage.Format_RGBA64).rgbSwapped()
            else: 
                qimg_patch = self.get_manual_projection(doc, crop_x, crop_y, crop_size, crop_size)
        if qimg_patch:
            self.camera_preview.update_image(qimg_patch)
            self.main_viewport.stamp_trail(qimg_patch, dest_rect, dest_rect)
            transform = self.get_current_view_transform()
            self.live_patch_ready.emit(qimg_patch, crop_x, crop_y, crop_size, crop_size, transform)

    def get_current_view_transform(self):
        try:
            view = self.app_ref.activeWindow().activeView()
            if not view: return QTransform()
            canvas = view.canvas()
            doc = self.app_ref.activeDocument()
            res = doc.resolution() or 72.0
            zoom = canvas.zoomLevel()
            rotation = canvas.rotation()
            mirror = canvas.mirror()
            scale_factor = (72.0 / res) * zoom
            t_flake = view.flakeToCanvasTransform()
            origin = t_flake.map(QPointF(0.0, 0.0))
            transform = QTransform()
            transform.translate(origin.x(), origin.y())
            transform.rotate(rotation)
            sx = -scale_factor if mirror else scale_factor
            sy = scale_factor
            transform.scale(sx, sy)
            return transform
        except:
            return QTransform()

    def map_pos_to_document_absolute(self, global_pos):
        try:
            app = self.app_ref
            doc = app.activeDocument()
            view = app.activeWindow().activeView()
            if not doc or not view: return None
            canvas = view.canvas()
            target_widget = QApplication.widgetAt(global_pos)
            if target_widget and target_widget != self.main_viewport: pass 
            else: target_widget = QApplication.focusWidget() or app.activeWindow().qwindow().centralWidget()
            if not target_widget: return None
            local_pos = target_widget.mapFromGlobal(global_pos)
            t_flake = view.flakeToCanvasTransform()
            origin = t_flake.map(QPointF(0.0, 0.0))
            res = doc.resolution() or 72.0
            zoom = canvas.zoomLevel()
            scale_factor = (72.0 / res) * zoom
            doc_x_scaled = local_pos.x() - origin.x()
            doc_y_scaled = local_pos.y() - origin.y()
            rotation = canvas.rotation()
            if rotation != 0:
                angle_rad = -rotation * 3.14159265359 / 180.0
                cos_a = cos(angle_rad)
                sin_a = sin(angle_rad)
                tx = doc_x_scaled * cos_a - doc_y_scaled * sin_a
                ty = doc_x_scaled * sin_a + doc_y_scaled * cos_a
                doc_x_scaled = tx
                doc_y_scaled = ty
            if canvas.mirror(): doc_x_scaled = -doc_x_scaled
            return QPointF(doc_x_scaled / scale_factor, doc_y_scaled / scale_factor)
        except: return None

# =========================================================================================
# CLASE 5: DOCKER PRINCIPAL
# =========================================================================================
class CameraMonitorDocker(DockWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Infinite Canvas")
        
        self.baseWidget = QWidget()
        self.vbox = QVBoxLayout()
        self.vbox.setContentsMargins(0,0,0,0)
        self.baseWidget.setLayout(self.vbox)
        self.setWidget(self.baseWidget)
        
        self.current_color = QColor(0, 0, 255) # Azul por defecto
        self.settings_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "infinite_canvas_settings.txt")

        # --- TOOLBAR 1 ---
        toolbar = QWidget()
        hbox = QHBoxLayout()
        hbox.setContentsMargins(4,4,4,4)
        toolbar.setLayout(hbox)
        
        self.btn_active = QToolButton()
        self.btn_active.setText("Enable")
        self.btn_active.setCheckable(True)
        self.btn_active.clicked.connect(self.toggle_tracking)
        self.btn_active.clicked.connect(self.save_settings)
        
        self.combo_size = QComboBox()
        self.combo_size.addItems(["Normal (1x)", "Wide (3x)", "Ultra (5x)"])
        self.combo_size.currentIndexChanged.connect(self.save_settings)
        self.combo_size.currentIndexChanged.connect(self.update_settings)
        
        self.chk_reticle = QCheckBox("Box")
        self.chk_reticle.toggled.connect(self.save_settings)
        self.chk_reticle.toggled.connect(self.update_visibility)
        
        self.combo_source = QComboBox()
        self.combo_source.addItems(["Current Layer", "Full Document"])
        self.combo_source.currentIndexChanged.connect(self.save_settings)
        self.combo_source.currentIndexChanged.connect(self.update_settings)
        self.combo_source.currentIndexChanged.connect(lambda: self.update_full_canvas(force=True))

        self.btn_color = QPushButton()
        self.btn_color.setFixedSize(20, 20)
        self.btn_color.setStyleSheet(f"background-color: {self.current_color.name()}; border: 1px solid gray;")
        self.btn_color.clicked.connect(self.select_color)
        
        hbox.addWidget(self.btn_active)
        hbox.addWidget(self.combo_size)
        hbox.addWidget(self.combo_source)
        hbox.addWidget(self.chk_reticle)
        hbox.addWidget(self.btn_color) 
        hbox.addStretch()
        self.vbox.addWidget(toolbar)

        # --- TOOLBAR 2 (OVERLAY SETTINGS) ---
        toolbar2 = QWidget()
        hbox2 = QHBoxLayout()
        hbox2.setContentsMargins(4,0,4,4)
        toolbar2.setLayout(hbox2)

        self.chk_overlay = QCheckBox("Overlay")
        self.chk_overlay.setStyleSheet("color: blue; font-weight: bold;")
        self.chk_overlay.toggled.connect(self.save_settings)
        self.chk_overlay.toggled.connect(self.toggle_overlay)

        self.chk_no_color = QCheckBox("No Color")
        self.chk_no_color.toggled.connect(self.save_settings)
        self.chk_no_color.toggled.connect(self.update_overlay_settings)

        self.chk_crop = QCheckBox("Crop")
        self.chk_crop.setToolTip("Ocultar contenido dentro del canvas")
        self.chk_crop.toggled.connect(self.save_settings)
        self.chk_crop.toggled.connect(self.update_overlay_settings)

        self.chk_outline = QCheckBox("Edges")
        self.chk_outline.toggled.connect(self.save_settings)
        self.chk_outline.toggled.connect(self.update_overlay_settings)

        self.slider_opacity = QSlider(Qt.Horizontal)
        self.slider_opacity.setRange(0, 100)
        self.slider_opacity.setFixedWidth(80)
        self.slider_opacity.setToolTip("Opacidad del Overlay")
        self.slider_opacity.valueChanged.connect(self.save_settings)
        self.slider_opacity.valueChanged.connect(self.update_overlay_settings)

        hbox2.addWidget(self.chk_overlay)
        hbox2.addWidget(self.chk_no_color)
        hbox2.addWidget(self.chk_crop)
        hbox2.addWidget(self.chk_outline)
        hbox2.addWidget(QLabel("Op:"))
        hbox2.addWidget(self.slider_opacity)
        hbox2.addStretch()
        self.vbox.addWidget(toolbar2)

        self.splitter = QSplitter(Qt.Vertical)
        self.vbox.addWidget(self.splitter)

        self.main_viewport = MainViewportWidget()
        self.splitter.addWidget(self.main_viewport)
        
        self.cam_container = QWidget()
        cam_layout = QVBoxLayout()
        cam_layout.setAlignment(Qt.AlignCenter)
        self.cam_container.setLayout(cam_layout)
        
        self.cam_label = QLabel("Preview")
        cam_layout.addWidget(self.cam_label)
        
        self.camera_preview = CameraPreviewWidget()
        cam_layout.addWidget(self.camera_preview)
        
        self.splitter.addWidget(self.cam_container)

        self.view_state = ViewState()
        self.interceptor = InputInterceptor(self.view_state, self.main_viewport, self.camera_preview)
        self.interceptor.stroke_finished.connect(self.on_stroke_finished)
        self.interceptor.live_patch_ready.connect(self.relay_patch_to_overlay)
        
        # --- CONEXIÓN A ACCIONES DE KRITA (Undo/Redo/Etc) ---
        try:
            action_names = ['edit_undo', 'edit_redo', 'clear', 'edit_cut', 'edit_paste']
            for name in action_names:
                ac = Krita.instance().action(name)
                if ac:
                    ac.triggered.connect(self.on_history_action)
        except Exception as e:
            print(f"Error conectando acciones: {e}")
        
        self.main_viewport.contentChanged.connect(self.refresh_overlay)
        
        self.app_instance = QApplication.instance()
        
        self.overlay = None
        self.target_viewport = None
        self.sync_timer = QTimer(self)
        self.sync_timer.timeout.connect(self.sync_overlay_geometry)
        
        self.monitor_timer = QTimer(self)
        self.monitor_timer.setInterval(100) 
        self.monitor_timer.timeout.connect(self.check_bounds_change)
        
        self.load_settings()
        self.update_settings()

    def select_color(self):
        color = QColorDialog.getColor(self.current_color, self, "Seleccionar Color Overlay")
        if color.isValid():
            self.current_color = color
            self.btn_color.setStyleSheet(f"background-color: {color.name()}; border: 1px solid gray;")
            self.update_overlay_settings()
            self.save_settings()

    def save_settings(self):
        try:
            # Check if button exists before accessing
            if not self.btn_active: return
            
            data = {
                "is_active": self.btn_active.isChecked(),
                "size_index": self.combo_size.currentIndex(),
                "source_index": self.combo_source.currentIndex(),
                "reticle": self.chk_reticle.isChecked(),
                "overlay": self.chk_overlay.isChecked(),
                "no_color": self.chk_no_color.isChecked(),
                "crop": self.chk_crop.isChecked(),
                "outline": self.chk_outline.isChecked(),
                "opacity": self.slider_opacity.value(),
                "color": self.current_color.name()
            }
            with open(self.settings_path, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            print(f"Error guardando configuración: {e}")

    def load_settings(self):
        if not os.path.exists(self.settings_path): return
        try:
            with open(self.settings_path, 'r') as f:
                data = json.load(f)
            self.blockSignals(True)
            self.combo_size.setCurrentIndex(data.get("size_index", 0))
            self.combo_source.setCurrentIndex(data.get("source_index", 0))
            self.chk_reticle.setChecked(data.get("reticle", True))
            self.chk_overlay.setChecked(data.get("overlay", False))
            self.chk_no_color.setChecked(data.get("no_color", False))
            self.chk_crop.setChecked(data.get("crop", False))
            self.chk_outline.setChecked(data.get("outline", False))
            self.slider_opacity.setValue(data.get("opacity", 100))
            color_name = data.get("color", "#0000ff")
            self.current_color = QColor(color_name)
            self.btn_color.setStyleSheet(f"background-color: {self.current_color.name()}; border: 1px solid gray;")
            was_active = data.get("is_active", False)
            self.btn_active.setChecked(was_active)
            if was_active:
                self.btn_active.setText("Disable")
                self.toggle_tracking()
            else:
                self.btn_active.setText("Enable")
            self.blockSignals(False)
            self.update_settings()
            self.update_visibility()
            self.update_overlay_settings()
        except Exception as e:
            print(f"Error cargando configuración: {e}")

    def canvasChanged(self, canvas):
        # SAFETY CHECK: Si los widgets han sido borrados, salir inmediatamente
        try:
            if not self.chk_overlay or not self.main_viewport: return
        except RuntimeError:
            return

        if canvas:
            self.update_full_canvas(force=True)
            
            # PROTECCIÓN DE ERROR "wrapped C/C++ object ... deleted"
            try:
                if self.chk_overlay.isChecked():
                    self.toggle_overlay(False)
                    
                    def safe_reenable():
                        try:
                            # Volvemos a chequear si existe el widget antes de usarlo
                            if self.chk_overlay and self.chk_overlay.isChecked():
                                self.toggle_overlay(True)
                        except RuntimeError:
                            pass

                    QTimer.singleShot(100, safe_reenable)
            except RuntimeError:
                pass
        else:
            try:
                self.main_viewport.set_base_background(None)
            except RuntimeError:
                pass

    def relay_patch_to_overlay(self, image, x, y, w, h, transform):
        try:
            if self.overlay and self.overlay.isVisible():
                self.overlay.handle_live_patch(image, x, y, w, h, transform)
        except RuntimeError:
            self.overlay = None

    def update_settings(self):
        size_idx = self.combo_size.currentIndex()
        mult = 1
        if size_idx == 1: mult = 3
        if size_idx == 2: mult = 5
        self.interceptor.set_multiplier(mult)
        mode = self.combo_source.currentIndex()
        self.interceptor.set_mode(mode)

    def update_visibility(self):
        visible = self.chk_reticle.isChecked()
        self.main_viewport.set_reticle_visible(visible)

    def update_overlay_settings(self):
        if self.overlay:
            try:
                opacity = self.slider_opacity.value() / 100.0
                crop = self.chk_crop.isChecked()
                outline = self.chk_outline.isChecked()
                no_color = self.chk_no_color.isChecked()
                mode = self.combo_source.currentIndex()
                self.overlay.set_overlay_settings(opacity, crop, outline, self.current_color, no_color, mode)
            except RuntimeError:
                self.overlay = None

    def toggle_tracking(self):
        is_active = self.btn_active.isChecked()
        if is_active:
            self.btn_active.setText("Disable")
            self.interceptor.active = True
            self.app_instance.installEventFilter(self.interceptor)
            self.monitor_timer.start() 
            self.update_full_canvas(force=True)
        else:
            self.btn_active.setText("Enable")
            self.interceptor.active = False
            self.app_instance.removeEventFilter(self.interceptor)
            self.monitor_timer.stop()

    def on_stroke_finished(self):
        self.view_state.last_bounds_hash = None
        
        def safe_update():
            try: self.update_full_canvas(force=True)
            except RuntimeError: pass

        self.update_full_canvas(force=True)
        QTimer.singleShot(100, safe_update)
        
    def on_history_action(self):
        self.view_state.last_bounds_hash = None
        
        def safe_update():
            try: self.update_full_canvas(force=True)
            except RuntimeError: pass
            
        QTimer.singleShot(100, safe_update)

    def check_bounds_change(self):
        if self.combo_source.currentIndex() == 1: return
        try:
            doc = Krita.instance().activeDocument()
            if not doc: return
            node = doc.activeNode()
            if not node: return
            bounds = node.bounds()
            x, y, w, h = bounds.x(), bounds.y(), bounds.width(), bounds.height()
            if w <= 0: x, y, w, h = 0, 0, doc.width(), doc.height()
            
            current_hash = (x, y, w, h)
            if current_hash != self.view_state.last_bounds_hash:
                self.update_full_canvas(force=True)
        except:
            pass

    def calculate_total_bounds(self, doc):
        total_rect = QRect()
        hay_contenido = False
        def crawl_bounds(node):
            nonlocal total_rect, hay_contenido
            for child in node.childNodes():
                if not child.visible(): continue
                if "Group" in child.type():
                    crawl_bounds(child)
                else:
                    b = child.bounds()
                    if not b.isEmpty():
                        if not hay_contenido:
                            total_rect = QRect(b)
                            hay_contenido = True
                        else:
                            total_rect = total_rect.united(b)
        root = doc.rootNode()
        if root:
            crawl_bounds(root)
        if not hay_contenido:
            return 0, 0, doc.width(), doc.height()
        return total_rect.x(), total_rect.y(), total_rect.width(), total_rect.height()

    def update_full_canvas(self, force=False):
        try:
            # SAFETY CHECK: Si main_viewport no existe, abortar
            if not self.main_viewport: return
            # Verificar si C++ object ha sido borrado
            _ = self.main_viewport.isVisible() 
        except RuntimeError:
            return

        try:
            doc = Krita.instance().activeDocument()
            if not doc: return
            mode = self.combo_source.currentIndex()
            if mode == 0:
                node = doc.activeNode()
                if not node: return
                bounds = node.bounds()
                x, y, w, h = bounds.x(), bounds.y(), bounds.width(), bounds.height()
                if w <= 0: x, y, w, h = 0, 0, doc.width(), doc.height()
            else:
                x, y, w, h = self.calculate_total_bounds(doc)

            self.view_state.last_bounds_hash = (x, y, w, h)
            scale_ratio = 1.0
            max_dim = max(w, h)
            if max_dim > MAX_BUFFER_SIZE:
                scale_ratio = MAX_BUFFER_SIZE / float(max_dim)
            target_w = int(w * scale_ratio)
            target_h = int(h * scale_ratio)
            
            if not self.main_viewport.trail_buffer or \
               self.main_viewport.trail_buffer.width() != target_w or \
               self.main_viewport.trail_buffer.height() != target_h:
                   self.main_viewport.init_buffers(target_w, target_h)

            self.view_state.src_rect = QRect(x, y, w, h)
            self.view_state.scale = scale_ratio
            self.view_state.offset_x = 0 
            self.view_state.offset_y = 0 
            self.view_state.valid = True
            
            if force or target_w > 0:
                full_img = None
                if mode == 0: 
                    node = doc.activeNode()
                    if node:
                        full_img = node.thumbnail(target_w, target_h)
                else:
                    full_res_img = self.interceptor.get_manual_projection(doc, x, y, w, h)
                    if full_res_img:
                        full_img = QImage(full_res_img)
                        if full_img.width() != target_w or full_img.height() != target_h:
                             full_img = full_img.scaled(
                                 target_w, target_h, 
                                 Qt.KeepAspectRatio, 
                                 Qt.SmoothTransformation
                             )
                if full_img:
                     self.main_viewport.set_base_background(QPixmap.fromImage(full_img))
                if self.overlay:
                    self.overlay.clear_live_buffer()
        except Exception as e:
            # Imprimir el error para debug, pero no romper la ejecución
            # print(f"Error en update_full_canvas: {e}")
            pass

    def refresh_overlay(self):
        try:
            if self.overlay and self.overlay.isVisible():
                self.overlay.update()
        except RuntimeError:
            self.overlay = None

    def find_canvas_viewport(self):
        try:
            win = Krita.instance().activeWindow()
            if not win: return None
            qwin = win.qwindow()
            mdi = qwin.findChild(QMdiArea)
            if mdi:
                sub = mdi.currentSubWindow()
                if sub:
                    scroll = sub.findChild(QAbstractScrollArea)
                    if scroll: return scroll.viewport()
                    vw = mdi.findChild(QWidget, "view_widget")
                    if vw: return vw
        except: pass
        return None

    def toggle_overlay(self, checked):
        # PROTECCIÓN COMPLETA CONTRA OBJETOS BORRADOS
        try:
            if checked:
                self.target_viewport = self.find_canvas_viewport()
                if self.target_viewport:
                    if self.overlay:
                        try: self.overlay.close()
                        except: pass
                    self.overlay = OverlayWidget(self, parent=self.target_viewport)
                    self.update_overlay_settings() 
                    self.overlay.show()
                    self.overlay.raise_()
                    self.sync_timer.start(16)
                else:
                    # Si no hay viewport, intentamos desmarcar, pero verificando que exista
                    if self.chk_overlay:
                        self.chk_overlay.setChecked(False)
            else:
                if self.overlay:
                    try: self.overlay.close()
                    except: pass
                self.overlay = None
                self.sync_timer.stop()
                if self.target_viewport:
                    try: self.target_viewport.update()
                    except: pass
        except RuntimeError:
            pass

    def sync_overlay_geometry(self):
        if not self.target_viewport: return
        if self.overlay:
            try:
                if not self.target_viewport.isVisible():
                    self.overlay.hide()
                else:
                    self.overlay.show()
                    rect = self.target_viewport.rect()
                    if self.overlay.geometry() != rect:
                        self.overlay.setGeometry(rect)
                        self.overlay.ensure_buffers()
                    self.overlay.update()
            except RuntimeError:
                self.overlay = None
                self.sync_timer.stop()
                try: self.chk_overlay.setChecked(False)
                except: pass

class CameraEraserFixFactory(DockWidgetFactoryBase):
    def __init__(self):
        super().__init__("infinite_canvas", DockWidgetFactoryBase.DockRight)

    def createDockWidget(self):
        return CameraMonitorDocker()

Krita.instance().addDockWidgetFactory(CameraEraserFixFactory())