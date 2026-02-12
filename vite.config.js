import { defineConfig } from 'vite';
import { resolve } from 'path';

export default defineConfig({
    build: {
        outDir: 'src/web/static',
        emptyOutDir: false,
        lib: {
            entry: resolve(__dirname, 'frontend/src/main.js'),
            name: 'VexoDashboard',
            fileName: () => 'dashboard.js',
            formats: ['iife']
        },
        rollupOptions: {
            output: {
                assetFileNames: (assetInfo) => {
                    if (assetInfo.name === 'style.css') return 'dashboard.css';
                    return assetInfo.name;
                }
            }
        }
    }
});
