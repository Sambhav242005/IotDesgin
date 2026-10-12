"""Saved-scene Viser viewer, following the official VGGT demo's point-cloud/camera approach."""
import json
import time

import numpy as np
import viser
import viser.transforms as tf


def load_scene(folder):
    manifest = json.loads((folder / 'scene.json').read_text())
    with np.load(folder / 'viewer.npz', allow_pickle=False) as data:
        points, colors = data['points'].copy(), data['colors'].copy()
    cameras = json.loads((folder / 'cameras.json').read_text())
    return manifest, points, colors, cameras


def run_viewer(folder, port=8080):
    manifest, points, colors, cameras = load_scene(folder)
    center = np.median(points, axis=0)
    extent = max(float(np.linalg.norm(np.percentile(points, 95, axis=0)
                                     - np.percentile(points, 5, axis=0))), 0.1)
    server = viser.ViserServer(host='127.0.0.1', port=port)
    server.gui.configure_theme(titlebar_content=None, control_layout='collapsible')
    cloud = server.scene.add_point_cloud('/room', points=points - center, colors=colors,
                                        point_size=extent / 1500, point_shape='circle')
    with np.load(folder / 'viewer.npz', allow_pickle=False) as arrays:
        semantic_colors = arrays['semantic_colors'].copy() if 'semantic_colors' in arrays else colors.copy()
    color_objects = server.gui.add_checkbox('Color by object regions', initial_value=False)
    @color_objects.on_update
    def toggle_object_colors(_):
        cloud.colors = semantic_colors if color_objects.value else colors
    reload_scene = server.gui.add_button('Reload reviewed geometry')
    @reload_scene.on_click
    def reload_geometry(_):
        nonlocal points, colors, semantic_colors
        _, points, colors, _ = load_scene(folder)
        with np.load(folder / 'viewer.npz', allow_pickle=False) as arrays:
            semantic_colors = arrays['semantic_colors'].copy() if 'semantic_colors' in arrays else colors.copy()
        with server.atomic():
            cloud.points = points - center
            cloud.colors = semantic_colors if color_objects.value else colors
    handles = []
    for index, camera in enumerate(cameras):
        matrix = np.array(camera['camera_to_world'])
        matrix[:3, 3] -= center
        transform = tf.SE3.from_matrix(matrix)
        intrinsic = np.array(camera['intrinsics'])
        handles.append(server.scene.add_camera_frustum(
            f'/cameras/{index}', fov=2 * np.arctan(camera['height'] / (2 * intrinsic[1, 1])),
            aspect=camera['width'] / camera['height'], scale=extent / 50,
            wxyz=transform.rotation().wxyz, position=transform.translation(), color=(210, 237, 148),
        ))
    show = server.gui.add_checkbox('Show camera positions', initial_value=True)
    @show.on_update
    def toggle(_):
        for handle in handles:
            handle.visible = show.value
    server.gui.add_markdown(f"# IoTDesign\n**Unscaled point cloud**\n\n{len(points):,} points · {len(cameras)} cameras")
    server.gui.add_markdown('```json\n' + json.dumps(manifest['metrics'], indent=2) + '\n```')
    reset = server.gui.add_button('Reset view')
    def position(client):
        client.camera.position = (extent / 2, -extent / 2, extent / 3)
        client.camera.look_at = (0, 0, 0)
        client.camera.up_direction = (0, -1, 0)
    @server.on_client_connect
    def connected(client):
        position(client)
    @reset.on_click
    def reset_view(_):
        for client in server.get_clients().values():
            position(client)
    downloads = ['cloud.ply', 'cameras.json', 'scene.json']
    if manifest['metrics'].get('segmentation'):
        server.gui.add_markdown('SAM 2 image regions available. Names, when present, are proposals. '
                                'IDs are per frame; motion and material are unconfirmed.')
        downloads.extend(['objects.json', 'objects.npz', 'objects-preview.jpg', 'objects-previews.zip'])
        if (folder / 'objects-review.json').is_file():
            downloads.extend(['objects-review.json', 'cloud-unfiltered.ply'])
    for filename in downloads:
        button = server.gui.add_button('Download ' + filename)
        def bind_download(name):
            def download(event):
                if event.client:
                    event.client.send_file_download(name, (folder / name).read_bytes())
            return download
        button.on_click(bind_download(filename))
    print(f'Viewer: http://127.0.0.1:{port}', flush=True)
    try:
        while True:
            time.sleep(1)
    finally:
        server.stop()
