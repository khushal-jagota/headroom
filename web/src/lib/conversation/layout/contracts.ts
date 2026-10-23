/** Rectangles use layout-viewport CSS pixels, including visual viewport displacement. */
export interface LayoutRectangle {
  top: number;
  left: number;
  width: number;
  height: number;
}

export interface VisibleHostBounds {
  top: number;
  bottom: number;
  left: number;
  right: number;
  height: number;
  width: number;
}
