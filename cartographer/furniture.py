"""Optional vector ornaments over the map; none reserve page margins."""
import math

import numpy as np
import shapely
from shapely.geometry import LineString, Polygon, Point, box

from .model import MM, layout, furniture_options
from .geography import projection
from build_map import BLACK, WHITE

# Page-space clearance, shared with the browser sketch through compass_artwork.
CONTRAST_MARGIN_MM = 1.5


def compass_artwork():
    """Original eight-point rose and a swashed N, in millimeters about (0, 0).

    The same vector paths are supplied to the browser, avoiding a raster icon
    or a platform-dependent decorative font. N stays upright beside north.
    """
    def radial(radius,angle):return [radius*math.sin(angle),-radius*math.cos(angle)]
    spokes=[]
    for i in range(8):
        angle=i*math.pi/4;length=10 if i==0 else 7.7 if i%2==0 else 5.5
        a=radial(2.0,angle-math.pi/8);b=radial(2.0,angle+math.pi/8);tip=radial(length,angle)
        spokes.append(dict(outline=[[0,0],a,tip,b,[0,0]],shade=[[0,0],a,tip,[0,0]]))
    def curve(a,b,c,d):
        t=np.linspace(0,1,25)[:,None]
        return ((1-t)**3*np.array(a)+3*(1-t)**2*t*np.array(b)+3*(1-t)*t*t*np.array(c)+t**3*np.array(d)).tolist()
    letter=[
        curve([-3.2,1.1],[-4.4,2.5],[-1.5,3.1],[-1.15,1.3])+[[-.45,-2.6]],
        curve([-.45,-2.6],[.2,-1.2],[.9,.8],[1.5,2.3]),
        [[1.5,2.3],[2.1,-1.8]]+curve([2.1,-1.8],[2.4,-3.5],[4.7,-3.1],[3.6,-1.9]),
        curve([-2,-2],[-1.2,-3.5],[-.1,-3.2],[.6,-2.5])]
    return dict(spokes=spokes,letter=letter,ring_radius=3.2,north_label_radius=14,edge_inset=22,
                contrast_margin_mm=CONTRAST_MARGIN_MM)


def compass_geometry(recipe):
    art=compass_artwork();x0,y0,x1,y1=np.array(layout(recipe)['content_mm'])*MM
    center=np.array([x1-art['edge_inset']*MM,y0+art['edge_inset']*MM])
    north=projection(recipe)[:,1];north=north/np.linalg.norm(north)
    # Rotate the regular rose so its emphasized point tracks projected north.
    matrix=np.column_stack([[-north[1],north[0]],-north])
    def rose(points):return np.asarray(points)@matrix.T*MM+center
    outlines=[Polygon(rose(s['outline'])) for s in art['spokes']]
    shades=[Polygon(rose(s['shade'])) for s in art['spokes']]
    anchor=center+north*art['north_label_radius']*MM
    letter=[LineString(np.array(p)*MM+anchor) for p in art['letter']]
    ring=Point(center).buffer(art['ring_radius']*MM,quad_segs=48)
    # A silhouette-shaped knockout, including the letter's own backing.
    letter_back=shapely.union_all(letter).convex_hull.buffer(CONTRAST_MARGIN_MM*MM)
    backing=shapely.union_all([ring,*outlines]).buffer(CONTRAST_MARGIN_MM*MM).union(letter_back)
    return dict(center=center,north=north,outlines=outlines,shades=shades,letter=letter,ring=ring,backing=backing)


def furniture_plan(draw,recipe,attribution,has_elevation):
    options=furniture_options(recipe)
    plan=dict(text=[],marks=[],rose=None)
    if not any(options.values()):return plan
    x0,y0,x1,y1=np.array(layout(recipe)['content_mm'])*MM
    pad=4*MM;left=x0+pad;right=x1-pad
    def text(label,x,y,size,available,font='sans',identity='map',halo_width=2.5):
        size=min(size,available/max(draw.fonts[font].text_length(label,fontsize=1),1))
        plan['text'].append(dict(text=label,p=(x,y),size=size,font=font,identity=identity,halo_width=halo_width))
    top=y0+pad
    available=right-left-(43*MM if options['compass'] else 0)
    if available<30*MM:
        available=right-left;top=y0+45*MM
    if options['title']:
        size=min(18,available/max(draw.fonts['serif'].text_length(recipe['title'],fontsize=1),1))
        top+=draw.fonts['serif'].ascender*size
        text(recipe['title'],left,top,size,available,'serif','title',halo_width=2*CONTRAST_MARGIN_MM*MM);top+=5*MM
    if options['parameters']:
        desc=f"1:{recipe['framing']['scale_denominator']:,.0f} at center"
        if recipe['camera']['relief']:
            c=recipe['camera'];desc+=f"  /  {c['elevation_deg']:g}° camera  /  {c['exaggeration']:g}× height"
        text(desc,left,top+8,8,available,identity='parameters')
    if options['compass']:
        plan['rose']=compass_geometry(recipe)
    bottom=y1-pad
    if options['signature']:
        text('Plotline',right-32,bottom,7,32,identity='signature')
        if not options['credits']:bottom-=10
    if options['credits']:
        text(attribution,left,bottom,6.8,right-left-(40 if options['signature'] else 0),identity='credits')
        bottom-=10
        if has_elevation:
            text('Elevation: Terrain Tiles · USGS / SRTM / other sources · tilezen.org/attribution',left,bottom,6.8,right-left,identity='elevation-credits')
            bottom-=10
    if options['legend']:
        bottom-=5
        for j,(role,label) in enumerate([('roads','Streets & crossings'),('parks','Parks'),('water','Shore & water')]):
            x=left+j*(right-left)/3
            mark=LineString([(x,bottom-3),(x+15,bottom-3)])
            plan['marks'].append((role,label,mark))
            text(label,x+21,bottom,8,(right-left)/3-25,identity='legend')
    return plan


def furniture_occupied(draw,plan):
    """Reserve label collision boxes only; map features still fill these areas."""
    occupied=[]
    for item in plan['text']:
        x,y=item['p'];size=item['size'];font=draw.fonts[item['font']]
        occupied.append(box(x,y-font.ascender*size,x+font.text_length(item['text'],fontsize=size),
                            y-font.descender*size).buffer(max(1*MM,item['halo_width']/2)))
    occupied.extend(mark.buffer(1*MM) for _,_,mark in plan['marks'])
    if plan['rose']:occupied.append(plan['rose']['backing'].buffer(1*MM))
    return occupied


def furniture(draw,recipe,attribution,has_elevation,plan=None):
    plan=plan if plan is not None else furniture_plan(draw,recipe,attribution,has_elevation)
    clip=box(*(np.array(layout(recipe)['content_mm'])*MM))
    rose=plan['rose']
    if rose:
        draw.paint(rose['backing'],'labels/furniture/background','compass',color=None,fill=WHITE)
        draw.commit(clip)
        draw.paint(rose['ring'].boundary,'labels/furniture','compass-ring',width=.45)
        for outline,shade in zip(rose['outlines'],rose['shades']):
            draw.paint(outline,'labels/furniture','compass',width=.55,fill=WHITE)
            draw.paint(shade,'labels/furniture','compass-shade',color=None,fill=BLACK)
        for line in rose['letter']:draw.paint(line,'labels/furniture','north-N',width=.32*MM)
        draw.commit(clip)
    for role,label,mark in plan['marks']:
        draw.paint(mark,'legend/'+role,label+'-halo',color=WHITE,width=2.5)
        draw.paint(mark,'legend/'+role,label,width=.65);draw.commit(clip)
    for item in plan['text']:
        # White glyph outlines followed by normal ink; no white document pen.
        draw.halo_text(**item);draw.commit(clip)
