if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port)
    except ImportError:
        logging.warning("Waitress not found, using Flask dev server (NOT FOR PRODUCTION).")
        app.run(host="0.0.0.0", port=port)
