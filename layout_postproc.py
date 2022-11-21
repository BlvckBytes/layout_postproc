#!/usr/bin/python3

import sys
import argparse
import os
import xml.etree.ElementTree as ET
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.graphics import renderPDF
from reportlab.lib.pagesizes import A4
from svg.path import parse_path, Line, Move, Close, CubicBezier, Arc
from svglib.svglib import svg2rlg

# FIXME: Further investivate the issue where the svg gets drawn in a slightly "pushed down" manner if it's height increases

# ////////////////////////// Configuration //////////////////////////

strip_tags = ['text'] # Text is sometimes visible outside of bounds, strip
ignore_tags = ['title', 'desc'] # Title and desc are meta-info,
temp_folder = '/tmp' # Path for temporary file storage

# //////////////////////////// Utilities ////////////////////////////

"""
Normalizes a tag string by splitting off it's namespace
"""
def normalize_tag(tagstr):
  return tagstr[tagstr.rindex('}') + 1:]

"""
Merges two bound keeping arrays by integrating the new information of
the source into the destination
"""
def merge_bounds(dest: list[float], src: list[float]):
  if src[0] is not None and (dest[0] is None or src[0] < dest[0]):
    dest[0] = src[0]
  if src[0] is not None and (dest[1] is None or src[1] < dest[1]):
    dest[1] = src[1]
  if src[0] is not None and (dest[2] is None or src[2] > dest[2]):
    dest[2] = src[2]
  if src[0] is not None and (dest[3] is None or src[3] > dest[3]):
    dest[3] = src[3]

"""
Updates a bound keeping array by integrating the new
information of a given point
"""
def update_bounds(dest: list[float], point: complex):
  if dest[0] is None or point.real < dest[0]:
    dest[0] = point.real
  if dest[1] is None or point.imag < dest[1]:
    dest[1] = point.imag
  if dest[2] is None or point.real > dest[2]:
    dest[2] = point.real
  if dest[3] is None or point.imag > dest[3]:
    dest[3] = point.imag

"""
Resolves the SVG's dimensions into width and height in millimeters, if possible
"""
def resolve_dimensions(root: ET.Element) -> tuple[float, float] | None:
  width = root.attrib['width']
  height = root.attrib['height']

  # Take millimeters as they are
  if width.endswith('mm') and height.endswith('mm'):
    return (float(width[:width.index('m')]), float(height[:height.index('m')]))

  # Transform centimeters into millimeters
  if width.endswith('cm') and height.endswith('cm'):
    return (float(width[:width.index('c')]) * 10, float(height[:height.index('c')]) * 10)

"""
Analyzes the SVG's scaling by reading it's width and height
as millimeters and calculating how many millimeters there
are per one user unit
"""
def analyze_scaling(root: ET.Element) -> tuple[float, float, float]:
  dim = resolve_dimensions(root)

  if dim is None:
    print("Could not resolve the SVG's dimensions, likely an unsupported unit")
    sys.exit(1)

  # The units of these values are now millimeters
  width = dim[0]
  height = dim[1]

  _, _, viewbox_width, viewbox_height = map(lambda x: float(x), root.attrib['viewBox'].split(' '))

  result = width / viewbox_width

  if height / viewbox_height != result:
    print('Unequal X/Y scaling is not supported')
    sys.exit(1)

  return (width, height, result)

"""
Manipulates both the start- and the endpoint of a given command
by offsetting them on both axies
"""
def manip_start_end(command, x_off: float, y_off: float):
  command.start = complex(command.start.real + x_off, command.start.imag + y_off)
  command.end = complex(command.end.real + x_off, command.end.imag + y_off)

"""
Decides on a position in mm (top left is (0|0)) on the page
where the SVG's top left position should be rendered at, based
on it's width, height, page-padding and position choice
"""
def decide_svg_xy(width: int, height: int, padding: int, position: str) -> tuple[int, int]:
  if position == 'TL':
    return (padding * mm, padding * mm)

  if position == 'TC':
    return (A4[0] / 2 - width * mm / 2, padding * mm)

  if position == 'TR':
    return (A4[0] - width * mm - padding * mm, padding * mm)

  if position == 'CL':
    return (padding * mm, A4[1] / 2 - height * mm / 2)

  if position == 'CC':
    return (A4[0] / 2 - width * mm / 2, A4[1] / 2 - height * mm / 2)

  if position == 'CR':
    return (A4[0] - width * mm - padding * mm, A4[1] / 2 - height * mm / 2)

  if position == 'BL':
    return (padding * mm, A4[1] - height * mm - padding * mm)

  if position == 'BC':
    return (A4[0] / 2 - width * mm / 2, A4[1] - height * mm - padding * mm)

  if position == 'BR':
    return (A4[0] - width * mm - padding * mm, A4[1] - height * mm - padding * mm)

# ///////////////////////// Element Handlers /////////////////////////

"""
Handles a path element by either manipulating every single command
within it's description by the axies offsets or by calculating the
min and max bounds of all coordinates available
"""
def handle_path(element: ET.Element, bounds_mode: bool, x_off: float, y_off: float) -> tuple[float, float, float, float]:
  # MinX, MinY, MaxX, MaxY
  bounds = [None, None, None, None]

  # Defines the path to be drawn
  # A path definition is a list of path commands where each command is
  # composed of a command letter and numeric parameters
  d = element.attrib['d']
  path = parse_path(d)

  supported_commands = [Line, Move, Close, CubicBezier, Arc]

  for command in path:
    if not type(command) in supported_commands:
      print(f'Encountered unsupported path command {type(command)}')
      sys.exit(1)

    if not bounds_mode:
      manip_start_end(command, x_off, y_off)
    else:
      update_bounds(bounds, command.start)
      update_bounds(bounds, command.end)

  if bounds_mode:
    return bounds

  element.attrib['d'] = path.d()

"""
Handles a circle element by either manipulating it's center point
by the axies offsets or by returning that center point
"""
def handle_circle(element: ET.Element, bounds_mode: bool, x_off: float, y_off: float) -> complex:
  if bounds_mode:
    return complex(float(element.attrib['cx']), float(element.attrib['cy']))

  element.attrib['cx'] = str(float(element.attrib['cx']) + x_off)
  element.attrib['cy'] = str(float(element.attrib['cy']) + y_off)

"""
Walk a group of elements recursively and either apply an axies
offset or calculate it's min and max bounds
"""
def walk_group(group: ET.Element, bounds_mode: bool, x_off: float = 0, y_off: float = 0):
  # MinX, MinY, MaxX, MaxY
  bounds = [None, None, None, None]

  # Loop all elements of this group
  for element in group:

    if element.tag in ignore_tags:
      continue

    if element.tag in strip_tags:
      group.remove(element)
      continue

    # Dive into the current group recursively
    if element.tag == 'g':
      group_bounds = walk_group(element, bounds_mode, x_off, y_off)

      if bounds_mode:
        merge_bounds(bounds, group_bounds)

      continue

    if element.tag == 'path':
      path_bounds = handle_path(element, bounds_mode, x_off, y_off)

      if bounds_mode:
        merge_bounds(bounds, path_bounds)

      continue

    if element.tag == 'circle':
      pos = handle_circle(element, bounds_mode, x_off, y_off)

      if bounds_mode:
        update_bounds(bounds, pos)

      continue

    print(f'Encountered unknown element "{element.tag}!')
    sys.exit(1)

  return bounds

# /////////////////////////// Entry Point ///////////////////////////

"""
The main entry point of this program
"""
def main():
  positions = [
    'TL', 'TC', 'TR',
    'CL', 'CC', 'CR',
    'BL', 'BC', 'BR'
  ]

  parser = argparse.ArgumentParser()
  parser.add_argument('input', help='Input SVG file')
  parser.add_argument('-rw', '--rect-width', default=5, type=int, help='Width of the enclosing rectangle in mm')
  parser.add_argument('-rd', '--rect-distance', default=1.5, type=float, help="Distance between the content and it's enclosing rectangle")
  parser.add_argument('-rc', '--rect-color', default='#000000', type=str, help='HEX color (including #) of the enclosing rectangle')
  parser.add_argument('-pp', '--page-padding', default=10, type=float, help='Padding of the page in mm')
  parser.add_argument('-pos', '--position', default=positions[0], choices=positions, help='Position of the content on the page')

  # Destructure parsed arguments
  args = parser.parse_args()
  input_path: str = args.input
  position: str = args.position
  rect_width_mm: float = args.rect_width
  rect_padding_mm: float = args.rect_distance
  rect_color: str = args.rect_color
  page_padding_mm: float = args.page_padding

  # Not an absolute path, preprend with the CWD
  if not input_path.startswith('/'):
    input_path = os.path.join(os.getcwd(), input_path)

  # Validate input file path
  if not os.path.isfile(input_path) or not input_path.endswith('.svg'):
    print(f'Invalid path specified: {input_path}')
    sys.exit(1)

  # If there's no rectangle, there's no padding either
  if rect_width_mm == 0:
    rect_padding_mm = 0

  tree = ET.parse(input_path)
  root = tree.getroot()

  # Iterate through all XML elements
  for elem in root.iter():
    # Remove a namespace URI in the element's name
    elem.tag = normalize_tag(elem.tag)

  # Loop all elements once in order to check the global bounds
  global_bounds = [None, None, None, None]
  for child in root:
    if child.tag == 'g':
      merge_bounds(global_bounds, walk_group(child, True))

  scaling_info = analyze_scaling(root)
  mm_per_uu = scaling_info[2]

  x_off = -global_bounds[0] + (1 / mm_per_uu) * (rect_width_mm + rect_padding_mm)
  y_off = -global_bounds[1] + (1 / mm_per_uu) * (rect_width_mm + rect_padding_mm)

  # Loop all elements again to now apply the movement
  for child in root:
    if child.tag != 'g':
      continue

    walk_group(child, False, x_off, y_off)

  # Calculate the width and height of the svg's content in user units
  uuwidth = global_bounds[2] - global_bounds[0]
  uuheight = global_bounds[3] - global_bounds[1]

  uuwidth += (1 / mm_per_uu) * (rect_padding_mm * 2 + rect_width_mm * 2)
  uuheight += (1 / mm_per_uu) * (rect_padding_mm * 2 + rect_width_mm * 2)

  mmwidth = mm_per_uu * uuwidth
  mmheight = mm_per_uu * uuheight

  # Apply the new width, height and viewBox to trim all whitespace
  root.attrib['width'] = f'{mmwidth}mm'
  root.attrib['height'] = f'{mmheight}mm'
  root.attrib['viewBox'] = f'0 0 {uuwidth} {uuheight}'

  # Generate a rectangle which encloses the content and has the specified thickness
  rect_width_uu = rect_width_mm * (1 / mm_per_uu)
  rect_elem = ET.Element('rect', {
    'x': str(rect_width_uu / 2),
    'y': str(rect_width_uu / 2),
    'width': str(uuwidth - rect_width_uu),
    'height': str(uuheight - rect_width_uu),
    'stroke': rect_color,
    'stroke-width': str(rect_width_uu),
    'fill': 'none'
  })

  # Append the rectangle inside a group container
  rect_container = ET.Element('g')
  rect_container.append(rect_elem)
  root.append(rect_container)

  temp_svg = os.path.join(temp_folder, 'layout_postproc.svg')

  # Write the modified XML into an output SVG file
  tree.write(temp_svg)

  # Read the temporary SVG into a reportlab compatible graphic
  svg_rlg = svg2rlg(temp_svg)

  out_path = os.path.join(os.path.dirname(input_path), 'layout_postproc.pdf')

  # Auto-rotate if it's height could be narrower
  do_rotate = svg_rlg.height > svg_rlg.width
  if do_rotate:
    # Origin seems to be left center... shift back after rotating
    svg_rlg.translate(svg_rlg.height, 0)
    svg_rlg.rotate(90)

    # Swap height and width
    buf = mmheight
    mmheight = mmwidth
    mmwidth = buf

  xy_pos = decide_svg_xy(mmwidth, mmheight, page_padding_mm, position)

  # Create a new blank A4 canvas at the output location and draw the temporary svg on it
  pdf_canvas = canvas.Canvas(out_path, pagesize=A4)

  # Sometimes the PCB is too close to the top, this issue is still unresolved...
  # Let's just add 3mm to make sure the printer doesn't cut it off.
  renderPDF.draw(svg_rlg, pdf_canvas, xy_pos[0], A4[1] - xy_pos[1] - mmheight * mm - 3 * mm)

  pdf_canvas.save()

if __name__ == '__main__':
  main()