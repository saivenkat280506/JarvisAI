const packager = require('electron-packager');
const path = require('path');

async function bundle() {
  console.log('Starting Electron compilation for Windows x64...');
  
  const options = {
    dir: __dirname,
    name: 'jarvis',
    platform: 'win32',
    arch: 'x64',
    icon: path.resolve(__dirname, 'jarvis_icon.ico'),
    overwrite: true,
    out: path.resolve(__dirname, 'dist-desktop'),
    // Exclude Python virtual environment, git metadata, and build directories to optimize package size
    ignore: [
      /backend\/\.venv/,
      /build/,
      /dist/,
      /dist-desktop/,
      /\.git/
    ]
  };

  try {
    const appPaths = await packager(options);
    console.log('\n=========================================');
    console.log('🎉 STANDALONE DESKTOP COMPILATION SUCCESS!');
    console.log('=========================================');
    console.log(`Executable created successfully at:\n${appPaths[0]}`);
  } catch (err) {
    console.error('\n❌ Packaging failed with error:');
    console.error(err);
    process.exit(1);
  }
}

bundle();
